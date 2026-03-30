# -*- coding: utf-8 -*-
"""网页通道抢购循环：Playwright 访问 goofish，规则与 App 通道共用。"""

import asyncio
import hashlib
import time
from typing import Set

from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Task, TaskRule, ItemRecord, ParsedSearchItem, Phone, GrabStatus
from app.core.item_record_status import status_from_buy_attempt
from app.core.xianyu_web import GoofishWebClient
from app.core import web_runtime_session as wrs
from app.core import notifications as match_notifications
from app.core.buyer import (
    _pct_safe,
    _log,
    _is_task_running,
    _sleep_with_stop_check,
    _task_rules_list,
    _item_matches_any_rule,
    _matched_keywords,
    _purchase_dedup_key,
    _prune_recently_purchased,
    _parse_price,
    _recently_purchased,
    _RECENT_PURCHASE_TTL,
    _consecutive_parse_failures,
)
from config import settings


async def run_web_task_loop(task_id: int, session_factory) -> None:
    """
    与 buyer.run_task_loop 并列：仅处理 channel=web 的任务。
    使用 GoofishWebClient 轮询搜索页、匹配规则后进入详情尝试下单。
    """
    _log(task_id, "INFO", "网页抢购循环已启动（goofish + Playwright）")
    while True:
        try:
            async with session_factory() as session:
                r = await session.execute(select(Task).options(selectinload(Task.rules)).where(Task.id == task_id))
                task = r.scalar_one_or_none()
                if not task or not task.is_running:
                    _log(task_id, "INFO", "已停止")
                    break
                phone = await session.get(Phone, task.phone_id)
                if not phone or not phone.is_active:
                    _log(task_id, "INFO", "运行环境不存在或已停用")
                    break
                if not (phone.device_id or "").startswith("web:"):
                    _log(task_id, "WARNING", "网页任务须绑定 device_id 以 web: 开头的运行环境")
                    await asyncio.sleep(settings.poll_interval)
                    continue

            try:
                sess_ver_at_enter = wrs.get_session_version()
                async with GoofishWebClient(log_cb=lambda lv, m, *a: _log(task_id, lv, m, *a)) as client:
                    kw = (task.keyword or "").strip()
                    if not kw:
                        _log(task_id, "WARNING", "搜索词为空")
                        await asyncio.sleep(settings.poll_interval)
                        continue
                    await client.open_search(kw)

                    while True:
                        if wrs.get_session_version() != sess_ver_at_enter:
                            _log(
                                task_id,
                                "INFO",
                                "检测到任务页更新或清除了网页登录 Cookie，重启浏览器会话以应用",
                            )
                            break
                        async with session_factory() as session:
                            t = await session.get(Task, task_id, options=[selectinload(Task.rules)])
                            if not t or not t.is_running:
                                _log(task_id, "INFO", "任务已停止，退出网页刷新循环")
                                return
                            rules_list = _task_rules_list(t)
                            refresh_interval = max(1, min(60, getattr(t, "refresh_interval_sec", 3) or 3))
                            current_keyword = (t.keyword or "").strip()

                        # 网页端只解析列表前 3 条，减轻 MTOP/DOM 与匹配开销
                        items = await client.list_search_items(limit=3, search_keyword=current_keyword)
                        if not items:
                            _consecutive_parse_failures[task_id] = _consecutive_parse_failures.get(task_id, 0) + 1
                            if _consecutive_parse_failures.get(task_id, 0) >= 3:
                                _log(task_id, "ERROR", "【网页连续3次未解析到商品，请检查登录态或页面结构】")
                                _consecutive_parse_failures[task_id] = 0
                            _log(task_id, "INFO", "本页未解析到商品")
                        else:
                            _consecutive_parse_failures[task_id] = 0
                            _log(task_id, "INFO", "本页解析到 %s 条商品", len(items))
                            # 与 App 通道相同格式，供管理页「运行控制台」按描述/价格列展示
                            for i, it in enumerate(items, 1):
                                desc = ((it.get("description") or "")[:60])
                                p = it.get("price")
                                price_str = str(p) if p is not None else "-"
                                _log(task_id, "INFO", "解析商品[%s] 描述=%s 价格=%s", i, _pct_safe(desc), price_str)

                        def _item_key(it):
                            d = it.get("description") or ""
                            p = it.get("price")
                            return hashlib.sha256("%s|%s|%s" % (task_id, d, p).encode()).hexdigest()[:32]

                        async with session_factory() as session:
                            r = await session.execute(
                                select(ParsedSearchItem.item_key).where(ParsedSearchItem.task_id == task_id)
                            )
                            existing_keys = set(row[0] for row in r.fetchall() if row[0])
                            saved = 0
                            for it in items:
                                key = _item_key(it)
                                if key in existing_keys:
                                    continue
                                existing_keys.add(key)
                                session.add(
                                    ParsedSearchItem(
                                        task_id=task_id,
                                        item_key=key,
                                        description=(it.get("description") or "")[:512],
                                        price=it.get("price"),
                                    )
                                )
                                saved += 1
                            await session.commit()
                        if saved:
                            _log(task_id, "INFO", "已保存 %s 条解析商品（去重后）", saved)

                        seen_this_refresh: Set[str] = set()
                        for idx, item in enumerate(items):
                            raw = item.get("description") or ""
                            href = item.get("href") or ""
                            item_id = hashlib.sha256(href.encode()).hexdigest()[:32] if href else "idx_%s" % idx
                            if item_id in seen_this_refresh:
                                continue
                            seen_this_refresh.add(item_id)
                            price = item.get("price") if item.get("price") is not None else _parse_price(raw)
                            if not _item_matches_any_rule(raw, price, rules_list):
                                continue

                            dedup_key = _purchase_dedup_key(task_id, raw, price)
                            _prune_recently_purchased()
                            if (task_id, dedup_key) in _recently_purchased:
                                if time.time() - _recently_purchased[(task_id, dedup_key)] < _RECENT_PURCHASE_TTL:
                                    continue

                            async with session_factory() as session:
                                r2 = await session.execute(
                                    select(ItemRecord)
                                    .where(ItemRecord.task_id == task_id)
                                    .order_by(desc(ItemRecord.created_at))
                                    .limit(3)
                                )
                                recent_records = list(r2.scalars().all())
                            recent_keys = {
                                ((rec.title or "").strip(), rec.price)
                                for rec in recent_records
                                if rec.status in (GrabStatus.GRABBED_BY_ME, GrabStatus.LOCKED_ITEM)
                            }
                            if ((raw or "")[:512].strip(), price) in recent_keys:
                                continue

                            matched_kw = _matched_keywords(raw, price, rules_list)
                            match_notifications.add_match_notification(task_id, (raw or "")[:512], price, matched_kw)
                            _log(task_id, "INFO", "网页通道：符合条件，进入详情尝试购买")
                            ok, msg = await client.goto_item_and_try_buy(href)
                            if ok:
                                _recently_purchased[(task_id, dedup_key)] = time.time()
                            _log(task_id, "INFO", "抢购结果: %s", msg)
                            async with session_factory() as session:
                                session.add(
                                    ItemRecord(
                                        task_id=task_id,
                                        item_id=item_id,
                                        title=raw[:512],
                                        price=price,
                                        status=status_from_buy_attempt(ok, msg),
                                    )
                                )
                                await session.commit()
                            try:
                                await client.open_search(current_keyword)
                            except Exception as e:
                                _log(task_id, "WARNING", "返回搜索页异常: %s", e)
                            await asyncio.sleep(2)
                            break

                        if await _sleep_with_stop_check(refresh_interval, task_id, session_factory):
                            _log(task_id, "INFO", "任务已停止")
                            return
                        try:
                            await client.reload_search(current_keyword)
                        except Exception as e:
                            _log(task_id, "WARNING", "刷新搜索异常: %s", e)

            except RuntimeError as e:
                _log(task_id, "ERROR", "Playwright 启动失败: %s", str(e))
                await asyncio.sleep(max(5.0, settings.poll_interval))
            except Exception as e:
                _log(task_id, "ERROR", "网页轮询异常: %s", str(e))
                await asyncio.sleep(settings.poll_interval)

        except Exception as e:
            _log(task_id, "ERROR", "网页任务外层异常: %s", str(e))
            await asyncio.sleep(settings.poll_interval)
