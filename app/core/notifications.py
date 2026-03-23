# -*- coding: utf-8 -*-
"""内存通知：识别到符合条件商品时推送给 Web 端弹框"""

import time
from typing import Optional

# 最大保留条数，避免内存无限增长
_MAX = 200
# [(id, task_id, description, price, created_at, matched_keywords), ...]
_notifications: list[tuple[int, int, str, Optional[float], float, list]] = []
_next_id = 1


def add_match_notification(
    task_id: int, description: str, price: Optional[float], matched_keywords: Optional[list] = None
) -> None:
    """识别到符合条件商品时调用，写入一条通知；matched_keywords 用于弹框高亮"""
    global _next_id, _notifications
    nid = _next_id
    _next_id += 1
    created = time.time()
    _notifications.append((nid, task_id, description, price, created, matched_keywords or []))
    if len(_notifications) > _MAX:
        _notifications.pop(0)


def get_recent(limit: int = 20, after_id: Optional[int] = None) -> list[dict]:
    """获取最近通知，after_id 为已读过的最大 id，只返回比它新的"""
    out = []
    for nid, task_id, desc, price, created, matched_kw in reversed(_notifications):
        if after_id is not None and nid <= after_id:
            continue
        out.append({
            "id": nid,
            "task_id": task_id,
            "description": (desc or "")[:512],
            "price": price,
            "created_at": created,
            "matched_keywords": matched_kw or [],
        })
        if len(out) >= limit:
            break
    return out
