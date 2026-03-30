# -*- coding: utf-8 -*-
"""抢购逻辑：按关键词轮询新发商品，价格筛选，自动下单并记录统计"""

import asyncio
import hashlib
import logging
import re
import time
from datetime import datetime
from typing import Set, Optional, Dict, Tuple

# 最近 3 分钟内已匹配且执行过购买的商品：(task_id, item_key) -> 时间戳，再次刷到则跳过购买
_recently_purchased: Dict[Tuple[int, str], float] = {}
_RECENT_PURCHASE_TTL = 180  # 秒
# 连续加载商品失败次数，用于连续 3 次失败时在控制台提示
_consecutive_parse_failures: Dict[int, int] = {}
# 停止检查间隔（秒），便于点击停止后尽快退出
_STOP_CHECK_INTERVAL = 2
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Task, TaskRule, ItemRecord, ParsedSearchItem, Phone, GrabStatus
from app.core.item_record_status import status_from_buy_attempt
from app.core.device import connect_device
from app.core.xianyu import XianyuDriver
from app.core import task_console as console
from app.core import notifications as match_notifications
from config import settings, BASE_DIR

logger = logging.getLogger("xianyu.buyer")

# 任务日志文件句柄：task_id -> file，开启 save_task_log 时写入 logs/
_task_log_files: dict = {}


def _log(task_id: int, level: str, msg: str, *args) -> None:
    """同时写 logger、控制台缓存，以及（若开启）任务日志文件"""
    text = msg % args if args else msg
    getattr(logger, level.lower(), logger.info)(msg, *args)
    console.append(task_id, level.upper(), text)
    if task_id in _task_log_files:
        try:
            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            _task_log_files[task_id].write("[%s] [%s] %s\n" % (ts, level.upper(), text))
            _task_log_files[task_id].flush()
        except Exception:
            pass


def _pct_safe(s: str) -> str:
    """避免描述中含 % 导致「解析商品」日志的 % 格式化报错。"""
    return (s or "").replace("%", "%%")


def _parse_price(text: str) -> Optional[float]:
    """从文本中解析价格，如 "¥12.5" -> 12.5"""
    if not text:
        return None
    m = re.search(r"[\d]+\.?[\d]*", text.replace(",", ""))
    return float(m.group()) if m else None


async def _is_task_running(task_id: int, session_factory) -> bool:
    """从 DB 读取任务是否仍在运行（用于停止按钮响应）"""
    try:
        async with session_factory() as session:
            t = await session.get(Task, task_id)
            return t is not None and bool(t.is_running)
    except Exception:
        return True


async def _sleep_with_stop_check(interval_sec: float, task_id: int, session_factory) -> bool:
    """
    分段 sleep，每 _STOP_CHECK_INTERVAL 秒检查一次 is_running。
    若已停止则提前结束并返回 True，否则睡满 interval_sec 返回 False。
    """
    elapsed = 0.0
    while elapsed < interval_sec:
        chunk = min(_STOP_CHECK_INTERVAL, interval_sec - elapsed)
        await asyncio.sleep(chunk)
        elapsed += chunk
        if not await _is_task_running(task_id, session_factory):
            return True
    return False


def _task_rules_list(task) -> list:
    """返回 (keywords_list, min_price, max_price) 列表：无 rules 时用任务旧字段拼一条"""
    if task.rules:
        return [
            ([k.strip() for k in (r.description_keyword or "").split(",") if k.strip()], r.min_price, r.max_price)
            for r in task.rules
        ]
    # 兼容：无规则时用任务单条描述+价格区间
    kw = (task.description_keyword or "").strip()
    keywords = [k.strip() for k in kw.split(",") if k.strip()]
    return [(keywords, task.min_price, task.max_price)]


def _item_matches_any_rule(raw: str, price: Optional[float], rules_list: list) -> bool:
    """每条内且（关键字全包含+价格在区间），条间或；满足任一条返回 True。规则比对忽略大小写。"""
    raw_lower = (raw or "").lower()
    for keywords, min_p, max_p in rules_list:
        if keywords and not all((kw or "").strip().lower() in raw_lower for kw in keywords):
            continue
        if min_p is not None and (price is None or price < min_p):
            continue
        if max_p is not None and (price is None or price > max_p):
            continue
        return True
    return False


def _matched_keywords(raw: str, price: Optional[float], rules_list: list) -> list:
    """返回命中的规则中的关键字列表（用于弹框高亮）；未命中返回空列表。"""
    raw_lower = (raw or "").lower()
    for keywords, min_p, max_p in rules_list:
        if keywords and not all((kw or "").strip().lower() in raw_lower for kw in keywords):
            continue
        if min_p is not None and (price is None or price < min_p):
            continue
        if max_p is not None and (price is None or price > max_p):
            continue
        return [kw.strip() for kw in keywords if (kw or "").strip()]
    return []


def _purchase_dedup_key(task_id: int, raw: str, price) -> str:
    """同一商品（任务+描述+价格）的去重键，用于 3 分钟内不重复购买"""
    return hashlib.sha256(f"{task_id}|{(raw or '')[:512]}|{price}".encode()).hexdigest()[:32]


def _prune_recently_purchased() -> None:
    """移除超过 TTL 的已购买记录"""
    now = time.time()
    expired = [k for k, ts in _recently_purchased.items() if now - ts > _RECENT_PURCHASE_TTL]
    for k in expired:
        _recently_purchased.pop(k, None)


def _task_screenshot_path(task_id: int) -> Optional[str]:
    """返回当前任务超时截图保存路径，并确保目录存在"""
    try:
        dir_path = BASE_DIR / "app" / "static" / "task_screenshots"
        dir_path.mkdir(parents=True, exist_ok=True)
        return str(dir_path / f"{task_id}_latest.png")
    except Exception:
        return None


def _write_ui_dump(task_id: int, step: str, xml_content: str) -> str:
    """将 UI 层级快照写入 logs/，返回写入路径"""
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / ("ui_dump_task%d_%s_%s.xml" % (task_id, step, ts))
    try:
        path.write_text(xml_content or "", encoding="utf-8")
        return str(path)
    except Exception as e:
        return "写入失败: %s" % e


async def run_task_loop(task_id: int, session_factory):
    """
    单任务循环：拉取任务与设备，搜索关键词，检测新商品，符合条件则尝试购买并落库统计。
    session_factory 为可调用对象，返回 AsyncSession（用于在后台线程中创建新 session）。
    """
    try:
        if (getattr(settings, "save_task_log", False) or getattr(settings, "debug_dump_ui", False)) and task_id not in _task_log_files:
            log_dir = BASE_DIR / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / ("task_%s_%s.log" % (task_id, ts))
            try:
                _task_log_files[task_id] = open(log_path, "w", encoding="utf-8")
                _task_log_files[task_id].write("# 任务 %s 运行日志 (save_task_log/debug_dump_ui 开启)\n" % task_id)
                _task_log_files[task_id].flush()
            except Exception as e:
                logger.warning("无法创建任务日志文件: %s", e)
        _log(task_id, "INFO", "抢购循环已启动")
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
                        _log(task_id, "INFO", "设备不存在或已停用")
                        break

                # 网页通道：走 goofish + Playwright，不连 ADB
                channel = (getattr(task, "channel", None) or "app").strip().lower()
                if channel == "web":
                    from app.core.buyer_web import run_web_task_loop

                    await run_web_task_loop(task_id, session_factory)
                    return

                device = connect_device(phone.device_id)
                if not device:
                    _log(task_id, "WARNING", "设备 %s 连接失败，%s 秒后重试", phone.device_id, settings.poll_interval)
                    await asyncio.sleep(settings.poll_interval)
                    continue

                on_dump = None
                if getattr(settings, "debug_dump_ui", False):
                    on_dump = lambda step, xml: _log(task_id, "DEBUG", "页面快照已保存: %s", _write_ui_dump(task_id, step, xml))
                def _timeout_screenshot_path():
                    return _task_screenshot_path(task_id)
                driver = XianyuDriver(
                    device,
                    log_cb=lambda level, msg, *args: _log(task_id, level, msg, *args),
                    on_dump=on_dump,
                    on_timeout_screenshot=_timeout_screenshot_path,
                )

                # 定时运行前先关闭手机已打开的应用
                driver.close_all_apps()

                # 1. 检查是否安装闲鱼；若检测未通过则尝试启动一次，启动成功则视为已安装（兼容检测误判）
                if not driver.is_app_installed():
                    if driver.start_app():
                        _log(task_id, "INFO", "安装检测未通过但启动闲鱼成功，继续执行")
                    else:
                        _log(task_id, "WARNING", "请先在该设备上安装闲鱼 App")
                        await asyncio.sleep(settings.poll_interval)
                        continue

                # 2. 闲鱼未在前台时：二次检测 + 短时等待前台，避免误判导致重复启动
                if not driver.is_app_running():
                    await asyncio.sleep(2)
                    if driver.is_app_running():
                        continue  # 二次检测已是闲鱼，视为已在前台
                    # 再等 2s 看是否已在前台（app_current 有时误报），避免误判时打「已在后台」并 start_app
                    if driver.try_wait_front(timeout=2):
                        continue
                    if driver.is_app_in_background():
                        _log(task_id, "INFO", "闲鱼已在后台，正在切到前台")
                    if not driver.start_app():
                        _log(task_id, "WARNING", "启动闲鱼失败；若已打开多个应用或浮窗，请将闲鱼切到手机前台后任务会自动继续")
                        await asyncio.sleep(settings.poll_interval)
                        continue
                    await asyncio.sleep(2)

                # 3. 检查是否已登录，否则提示用户登录
                if not driver.is_logged_in():
                    _log(task_id, "WARNING", "请先在设备上登录闲鱼")
                    await asyncio.sleep(settings.poll_interval)
                    continue

                run_mode = getattr(task, "run_mode", None) or "from_app"
                if run_mode == "new_drop_only":
                    # 仅新发/降价模式：不执行定位首页与搜索，要求当前已在带「新发」「降价」的结果页
                    if not driver.is_on_new_drop_page():
                        _log(task_id, "WARNING", "仅新发/降价模式：请先手动进入搜索结果的「新发」页，%s 秒后重试", settings.poll_interval)
                        await asyncio.sleep(settings.poll_interval)
                        continue
                    _log(task_id, "INFO", "仅新发/降价模式，直接进入刷新循环")
                else:
                    # 从 App 开始定位：首页 → 填词 → 搜索 → 新发
                    if not driver.ensure_home_then_search(task.keyword):
                        _log(task_id, "WARNING", "搜索失败，%s 秒后重试", settings.poll_interval)
                        await asyncio.sleep(settings.poll_interval)
                        continue
                    await asyncio.sleep(settings.search_cooldown)

                # 新发页循环：按任务设置的刷新间隔刷新、解析、落库；匹配规则每轮从 DB 重载
                grabbed = False
                while True:
                    async with session_factory() as session:
                        t = await session.get(Task, task_id, options=[selectinload(Task.rules)])
                        if not t or not t.is_running:
                            _log(task_id, "INFO", "任务已停止，退出新发页循环")
                            break
                        rules_list = _task_rules_list(t)
                        refresh_interval = max(1, min(60, getattr(t, "refresh_interval_sec", 3) or 3))
                        current_keyword = (t.keyword or "").strip()  # 供刷新跑偏时重新定位到搜索列表
                    skipped_price_tab = False
                    try:
                        driver.set_current_keyword(current_keyword)
                        if driver.is_on_home_new_tab():
                            _log(task_id, "WARNING", "【自检】检测到处于主页「新发」tab，非搜索页，终止刷新并重新定位")
                            break
                        if not driver.is_on_new_drop_page():
                            if not driver._ensure_on_search_list():
                                _log(task_id, "WARNING", "【自检】当前不在「降价-新发」搜索页且恢复失败，终止刷新并重新定位")
                                break
                        # 当前在价格页时先切到新发再解析，确保每轮都能识别商品（新发/降价解析，价格不解析）
                        if driver.is_on_price_tab():
                            driver._click_tab("新发")
                            time.sleep(0.5)
                        skipped_price_tab = not driver.should_parse_items()
                        if skipped_price_tab:
                            items = []
                            _log(task_id, "INFO", "当前在「价格」tab，跳过解析商品")
                        else:
                            # 解析列表可能阻塞约 30s，放 executor 中并每 _STOP_CHECK_INTERVAL 秒检查 is_running，便于停止按钮尽快生效
                            loop = asyncio.get_running_loop()
                            # 与前 3 条解析策略一致，避免匹配项在第 3 条及以后时被截断导致只识别不下单
                            future = loop.run_in_executor(None, lambda: driver.get_search_result_items(limit=3))
                            items = []
                            while True:
                                done, _ = await asyncio.wait(
                                    [future, asyncio.sleep(_STOP_CHECK_INTERVAL)],
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if future in done:
                                    try:
                                        items = future.result() or []
                                    except Exception:
                                        items = []
                                    break
                                if not await _is_task_running(task_id, session_factory):
                                    items = []
                                    break
                        if not items and not await _is_task_running(task_id, session_factory):
                            _log(task_id, "INFO", "任务已停止，退出新发页循环")
                            break
                    except Exception:
                        items = []
                    if not items:
                        # 价格 tab 主动跳过解析不记为连续失败
                        if not skipped_price_tab:
                            _consecutive_parse_failures[task_id] = _consecutive_parse_failures.get(task_id, 0) + 1
                        if not skipped_price_tab and _consecutive_parse_failures.get(task_id, 0) >= 3:
                            _log(task_id, "ERROR", "【连续3次加载商品失败，请检查设备或页面】")
                            _consecutive_parse_failures[task_id] = 0
                        _log(task_id, "INFO", "本页未解析到商品")
                    else:
                        _consecutive_parse_failures[task_id] = 0
                        _log(task_id, "INFO", "本页解析到 %s 条商品", len(items))
                        for i, it in enumerate(items, 1):
                            desc = (it.get("description") or "")[:60]
                            price = it.get("price")
                            price_str = str(price) if price is not None else "-"
                            _log(task_id, "INFO", "解析商品[%s] 描述=%s 价格=%s", i, _pct_safe(desc), price_str)
                        # 去重保存：同商品（description+price）不重复落库，并记录到统计页
                        def _item_key(it):
                            d = it.get("description") or ""
                            p = it.get("price")
                            return hashlib.sha256(f"{task_id}|{d}|{p}".encode()).hexdigest()[:32]
                        async with session_factory() as session:
                            r = await session.execute(select(ParsedSearchItem.item_key).where(ParsedSearchItem.task_id == task_id))
                            existing_keys = set(row[0] for row in r.fetchall() if row[0])
                            saved = 0
                            for it in items:
                                key = _item_key(it)
                                if key in existing_keys:
                                    continue
                                existing_keys.add(key)
                                rec = ParsedSearchItem(
                                    task_id=task_id,
                                    item_key=key,
                                    description=it.get("description") or "",
                                    price=it.get("price"),
                                )
                                session.add(rec)
                                saved += 1
                            await session.commit()
                        if saved:
                            _log(task_id, "INFO", "已保存 %s 条解析商品（去重后）", saved)

                    # 每轮刷新单独去重：勿跨轮缓存 item_id，否则上一轮「未点中/未匹配」会永久阻止同一卡片再进下单逻辑
                    seen_this_refresh: Set[str] = set()
                    for idx, item in enumerate(items):
                        # 兼容 XML 解析结果（仅有 description/price）与 u2 兜底（text/desc）
                        raw = (item.get("text") or "") + (item.get("desc") or "") or (item.get("description") or "")
                        item_id = str(hash(raw)) if raw else f"idx_{idx}"
                        if item_id in seen_this_refresh:
                            continue
                        seen_this_refresh.add(item_id)
                        price = item.get("price") if item.get("price") is not None else _parse_price(raw)
                        if not _item_matches_any_rule(raw, price, rules_list):
                            continue

                        # 3 分钟内已对该商品执行过购买则跳过，避免重复下单
                        dedup_key = _purchase_dedup_key(task_id, raw, price)
                        _prune_recently_purchased()
                        if (task_id, dedup_key) in _recently_purchased:
                            if time.time() - _recently_purchased[(task_id, dedup_key)] < _RECENT_PURCHASE_TTL:
                                _log(task_id, "INFO", "3分钟内已对该商品执行过购买，跳过: 描述=%s 价格=%s", (raw or "")[:40], price)
                                continue

                        # 匹配到商品：先查最近 3 条购买记录，已存在则跳过避免重复
                        async with session_factory() as session:
                            r = await session.execute(
                                select(ItemRecord)
                                .where(ItemRecord.task_id == task_id)
                                .order_by(desc(ItemRecord.created_at))
                                .limit(3)
                            )
                            recent_records = list(r.scalars().all())
                        # 仅「已确认由我抢到 / 锁定待确认」时跳过；被抢/失败记录允许下一轮重试
                        recent_keys = {
                            ((rec.title or "").strip(), rec.price)
                            for rec in recent_records
                            if rec.status in (GrabStatus.GRABBED_BY_ME, GrabStatus.LOCKED_ITEM)
                        }
                        if ((raw or "")[:512].strip(), price) in recent_keys:
                            _log(task_id, "INFO", "该商品已在最近记录中（已抢到或锁定），跳过: 描述=%s 价格=%s", (raw or "")[:40], price)
                            continue

                        # 匹配到商品：暂停刷新任务，转入自动购买；并通知 Web 端弹框（带匹配关键字供高亮）
                        matched_kw = _matched_keywords(raw, price, rules_list)
                        match_notifications.add_match_notification(task_id, (raw or "")[:512], price, matched_kw)
                        _log(task_id, "INFO", "符合条件，暂停刷新任务，转入自动购买")
                        _log(task_id, "INFO", "点击第 %s 个商品进入详情并抢购（rv_index=%s）: 描述=%s 价格=%s",
                             idx + 1, item.get("rv_index"), (raw or "")[:50], price)
                        try:
                            ok, msg = driver.click_item_at_index_and_buy(idx, rv_index=item.get("rv_index"))
                            # 仅在自动化判定进入下单成功路径后节流，失败则下轮刷新可再试
                            if ok:
                                _recently_purchased[(task_id, dedup_key)] = time.time()
                            _log(task_id, "INFO", "抢购结果: %s", msg)
                            async with session_factory() as session:
                                record = ItemRecord(
                                    task_id=task_id,
                                    item_id=item_id,
                                    title=raw[:512],
                                    price=price,
                                    status=status_from_buy_attempt(ok, msg),
                                )
                                session.add(record)
                                await session.commit()
                            driver.back_to_search_list()
                            _log(task_id, "INFO", "购买结束（成功或失败），再次启动刷新任务")
                        except Exception as e:
                            _log(task_id, "WARNING", "抢购或返回列表异常: %s，将重新定位后继续", str(e))
                            logger.exception("任务 %s 抢购/返回异常: %s", task_id, e)
                            try:
                                driver.back_to_search_list()
                            except Exception:
                                pass
                            break
                        await asyncio.sleep(2)
                        grabbed = True
                        break
                    # 未匹配到或本次抢购完成后：继续循环，先等 refresh_interval 再刷新列表（传 keyword 供跑偏恢复）；分段 sleep 以便停止按钮能尽快生效
                    if await _sleep_with_stop_check(refresh_interval, task_id, session_factory):
                        _log(task_id, "INFO", "任务已停止，退出新发页循环")
                        break
                    try:
                        driver.refresh_search_list(current_keyword)
                        if driver.is_on_home_new_tab():
                            _log(task_id, "WARNING", "【自检】刷新后处于主页「新发」tab，终止刷新并重新定位")
                            break
                    except Exception as e:
                        _log(task_id, "WARNING", "刷新列表异常: %s，本轮回退后重试", str(e))

                # 新发页循环结束，回到外轮询
                await asyncio.sleep(settings.poll_interval)
            except asyncio.CancelledError:
                _log(task_id, "INFO", "被取消")
                break
            except Exception as e:
                _log(task_id, "ERROR", "轮询异常: %s", str(e))
                logger.exception("任务 %s 轮询异常: %s", task_id, e)
                await asyncio.sleep(settings.poll_interval)
    finally:
        if task_id in _task_log_files:
            try:
                _task_log_files.pop(task_id).close()
            except Exception:
                pass


def get_session_factory():
    """返回可在异步任务中使用的 session 工厂"""
    from app.db.database import AsyncSessionLocal
    return AsyncSessionLocal
