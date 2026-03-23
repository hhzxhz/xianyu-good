#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断网页通道：打开搜索页，统计 MTOP / DOM 解析条数（不启动 FastAPI）。"""
import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

# 诊断时尽量可见错误
os.environ.setdefault("PLAYWRIGHT_HEADLESS", "true")


async def main() -> None:
    from app.core.xianyu_web import GoofishWebClient

    kw = sys.argv[1] if len(sys.argv) > 1 else "钢笔"
    logs: list = []

    def logcb(lv, msg, *a):
        line = msg % a if a else msg
        logs.append("[%s] %s" % (lv, line))
        print("[%s] %s" % (lv, line), flush=True)

    async with GoofishWebClient(log_cb=logcb) as client:
        await client.open_search(kw)
        ok = await client.wait_for_mtop_search_data(timeout_ms=15000)
        print("wait_for_mtop_search_data:", ok, flush=True)
        pl = client._mtop_search_payload
        if pl:
            rl = (pl.get("data") or {}).get("resultList")
            print("resultList len:", len(rl) if isinstance(rl, list) else type(rl), flush=True)
        items = await client.list_search_items(limit=15, search_keyword=kw)
        print("list_search_items count:", len(items), flush=True)
        for i, it in enumerate(items[:5], 1):
            print(" ", i, (it.get("description") or "")[:50], it.get("price"), it.get("href", "")[:60])
    if not items:
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
