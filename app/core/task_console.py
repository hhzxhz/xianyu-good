# -*- coding: utf-8 -*-
"""运行中任务的控制台日志缓存，供 API 与管理页展示"""

import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

# task_id -> deque of {"time": str, "level": str, "msg": str, "ts": float}，最多保留条数
_MAX_LINES = 500
_store: dict[int, deque] = {}
_lock = threading.Lock()


def init_task(task_id: int) -> None:
    """任务启动时调用，为该任务创建或清空缓冲区"""
    with _lock:
        _store[task_id] = deque(maxlen=_MAX_LINES)


def append(task_id: int, level: str, msg: str) -> None:
    """追加一条控制台日志；time 为完整时间，ts 供前端判断「超过 1 分钟无商品日志」"""
    with _lock:
        if task_id not in _store:
            _store[task_id] = deque(maxlen=_MAX_LINES)
        now = datetime.now()
        t_short = now.strftime("%H:%M:%S")
        t_full = now.strftime("%Y-%m-%d %H:%M:%S")
        _store[task_id].append({
            "time": t_short,
            "time_full": t_full,
            "level": level,
            "msg": msg,
            "ts": time.time(),
        })


def get_lines(task_id: Optional[int] = None, limit: int = 300) -> list[dict]:
    """获取控制台行。task_id 为空则返回所有有日志的任务（带 task_id 前缀）"""
    with _lock:
        if task_id is not None:
            if task_id not in _store:
                return []
            lines = list(_store[task_id])
            return [{"task_id": task_id, **x} for x in lines[-limit:]]
        out = []
        per = max(limit // max(len(_store), 1), 20)
        for tid in sorted(_store.keys()):
            deq = _store[tid]
            for item in list(deq)[-per:]:
                out.append({
                    "task_id": tid,
                    "time": item["time"],
                    "time_full": item.get("time_full") or item["time"],
                    "level": item["level"],
                    "msg": item["msg"],
                    "ts": item.get("ts"),
                })
        return out[-limit:]