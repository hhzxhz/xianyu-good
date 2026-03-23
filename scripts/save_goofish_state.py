#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在本机打开 Chromium，手动登录闲鱼网页后按回车，将登录态写入 goofish_state.json。
在 .env 中设置 WEB_STORAGE_STATE_PATH=./goofish_state.json（或绝对路径）后重启服务。
"""
import asyncio
import sys
from pathlib import Path


async def main() -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("请先执行: pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(1)
    out = Path(__file__).resolve().parent.parent / "goofish_state.json"
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])
        context = await browser.new_context(locale="zh-CN")
        page = await context.new_page()
        await page.goto("https://www.goofish.com/", wait_until="domcontentloaded")
        input("请在浏览器中完成登录，回到此终端按回车保存登录态…\n")
        await context.storage_state(path=str(out))
        await browser.close()
    print("已写入: %s" % out)


if __name__ == "__main__":
    asyncio.run(main())
