# -*- coding: utf-8 -*-
"""闲鱼 PC 搜索 MTOP 响应解析：从 idlemtopsearch.pc.search 的 JSON 提取列表项。"""

import re
from typing import Any, Dict, List, Optional, Tuple

from config import settings


def _fleamarket_item_id(target_url: str) -> str:
    """从 fleamarket://item?id= 或 query 中解析数字商品 id。"""
    if not target_url:
        return ""
    m = re.search(r"[?&]id=(\d+)", target_url)
    return m.group(1) if m else ""


def _pick_title(ex: Dict[str, Any]) -> str:
    """从 exContent 取标题（与接口字段顺序一致，优先短字段）。"""
    detail = ex.get("detailParams") or {}
    t = (detail.get("title") or ex.get("title") or "").strip()
    if t:
        return t
    span = ex.get("titleSpan") or {}
    t = (span.get("content") or "").strip()
    if t:
        return t
    for block in ex.get("richTitle") or []:
        if not isinstance(block, dict) or block.get("type") != "Text":
            continue
        data = block.get("data") or {}
        tx = (data.get("text") or "").strip()
        if tx:
            return tx
    return ""


def _pick_price(ex: Dict[str, Any], main: Dict[str, Any]) -> Optional[float]:
    """售价：detailParams.soldPrice > clickParam.args.price。"""
    detail = ex.get("detailParams") or {}
    sp = detail.get("soldPrice")
    if sp is not None and str(sp).strip() != "":
        try:
            return float(str(sp).replace(",", ""))
        except ValueError:
            pass
    cp = main.get("clickParam") or {}
    args = cp.get("args") or {}
    p = args.get("price")
    if p is not None and str(p).strip() != "":
        try:
            return float(str(p).replace(",", ""))
        except ValueError:
            pass
    return None


def _pick_item_id(ex: Dict[str, Any], main: Dict[str, Any]) -> str:
    """商品数字 id：exContent / detailParams / args / targetUrl。"""
    detail = ex.get("detailParams") or {}
    for key in ("itemId",):
        v = ex.get(key) or detail.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    cp = main.get("clickParam") or {}
    args = cp.get("args") or {}
    for key in ("item_id", "id"):
        v = args.get(key)
        if v is not None and str(v).strip():
            return str(v).strip()
    return _fleamarket_item_id(str(main.get("targetUrl") or ""))


def _main_and_ex_from_row(entry: dict) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    从 resultList 单条中取 main / exContent（兼容少数字段挂在 data 下的变体）。

    :param entry: resultList 元素
    :return: (main, exContent)
    """
    cell = entry.get("data") if isinstance(entry, dict) else None
    if not isinstance(cell, dict):
        return {}, {}
    item_block = cell.get("item") or {}
    main = item_block.get("main") or {}
    if not main and isinstance(cell.get("main"), dict):
        main = cell["main"]
    ex = main.get("exContent") or {}
    if not ex and isinstance(cell.get("exContent"), dict):
        ex = cell["exContent"]
    return main, ex


def parse_pc_search_items(payload: dict, limit: int, search_keyword: str) -> List[Dict[str, Any]]:
    """
    解析 mtop.taobao.idlemtopsearch.pc.search 返回体中的 resultList。

    :param payload: 接口完整 JSON（含 data.resultList）
    :param limit: 最多条数
    :param search_keyword: 搜索词（预留与 DOM 路径一致，当前不做强过滤）
    :return: 与 list_search_items 相同结构：href, description, price, list_index
    """
    _ = search_keyword  # 与网页列表同源，一般无需再按关键词丢数据
    data = (payload or {}).get("data") or {}
    rows = data.get("resultList") or []
    if not isinstance(rows, list):
        return []

    base = (getattr(settings, "goofish_base_url", None) or "https://www.goofish.com").rstrip("/")
    out: List[Dict[str, Any]] = []
    for entry in rows:
        if not isinstance(entry, dict):
            continue
        main, ex = _main_and_ex_from_row(entry)
        item_id = _pick_item_id(ex, main)
        title = _pick_title(ex)
        if not item_id and not title:
            continue
        price = _pick_price(ex, main)
        href = ("%s/item?id=%s" % (base, item_id)) if item_id else ""
        desc = (title or "")[:500]
        out.append({"href": href, "description": desc, "price": price, "list_index": len(out)})
        if len(out) >= limit:
            break
    return out
