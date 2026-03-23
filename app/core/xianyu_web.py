# -*- coding: utf-8 -*-
"""闲鱼网页 goofish.com：Playwright 打开搜索页、解析列表、进入详情尝试点击购买。"""

import asyncio
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.core.goofish_curl_cookies import load_storage_state_from_curl_file
from app.core.web_runtime_session import get_playwright_storage_state
from app.core.goofish_mtop_parse import parse_pc_search_items
from app.core.goofish_mtop_pc_search import request_pc_search_list
from app.core.goofish_mtop_pc_detail import request_pc_item_detail
from config import settings

LogCb = Optional[Callable[[str, str], None]]


def _item_id_from_href(href: str) -> str:
    """
    从商品详情链接解析数字 itemId（用于 MTOP pc.detail）。

    :param href: 含 id= 或 item.htm?id= 的 URL
    :return: 数字串，无法解析时空串
    """
    if not href:
        return ""
    m = re.search(r"[?&]id=(\d+)", href, re.I)
    return m.group(1) if m else ""


# 降低 headless 被识别后整页只返回「非法访问」文案的概率（需在首次 goto 前注入）
_GOOFISH_STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
if (!window.chrome) { window.chrome = { runtime: {} }; }
"""

# 与常见桌面 Chrome 一致，避免使用 Playwright 默认 UA 触发风控
_DEFAULT_GOOFISH_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


def _mtop_ret_indicates_success(body: dict) -> bool:
    """
    判断 MTOP 外层 ret 是否为成功（失败时常为 RGV587 / 挤爆 等，且不应写入列表缓存）。

    :param body: 接口 JSON 根对象
    :return: 是否视为调用成功
    """
    ret = body.get("ret")
    if isinstance(ret, str):
        return "SUCCESS" in ret
    if isinstance(ret, list):
        return any(isinstance(x, str) and "SUCCESS" in x for x in ret)
    return False


def _is_nav_or_category_blob(text: str) -> bool:
    """
    判断是否为页头导航/类目拼成的一整段文案（误当商品）。
    特征：过长且命中多个站点导航词。
    """
    if not text or len(text) < 40:
        return False
    markers = (
        "登录",
        "搜索",
        "消息",
        "手机数码",
        "电脑服饰",
        "卡券潮玩",
        "图书游戏",
        "电动车",
        "租",
    )
    hits = sum(1 for m in markers if m in text)
    return hits >= 3


def _parse_price_from_text(text: str) -> Optional[float]:
    """从卡片文案中提取首个数字价格。"""
    if not text:
        return None
    m = re.search(r"¥\s*([\d,]+\.?\d*)|([\d,]+\.?\d*)\s*元", text.replace("\n", " "))
    if m:
        s = (m.group(1) or m.group(2) or "").replace(",", "")
        try:
            return float(s)
        except ValueError:
            return None
    m2 = re.search(r"[\d]+\.?[\d]*", text.replace(",", ""))
    return float(m2.group()) if m2 else None


class GoofishWebClient:
    """
    异步上下文管理器：启动 Chromium，加载可选 storage_state（登录态），
    在搜索页抓取商品链接与文案。
    """

    def __init__(self, log_cb: LogCb = None):
        self._log = log_cb
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        # 最近一次 PC 搜索 MTOP 响应（含 data.resultList），由 response 监听写入
        self._mtop_search_payload: Optional[dict] = None

    async def wait_for_mtop_search_data(self, timeout_ms: int = 15000) -> bool:
        """
        等待 response 监听器写入含 data.resultList 的 MTOP 搜索 JSON。

        :param timeout_ms: 最长等待毫秒
        :return: 是否等到有效载荷
        """
        step = 100
        elapsed = 0
        while elapsed < timeout_ms:
            pl = self._mtop_search_payload
            if pl is not None:
                data = pl.get("data") or {}
                if isinstance(data, dict) and "resultList" in data:
                    return True
            await self._page.wait_for_timeout(step)
            elapsed += step
        return False

    def _log_step(self, level: str, msg: str, *args) -> None:
        text = msg % args if args else msg
        if self._log:
            self._log(level.upper(), text)

    def _schedule_mtop_capture(self, response: Any) -> None:
        """Playwright 同步回调中投递异步任务，抓取搜索列表 JSON。"""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._capture_mtop_pc_search(response))

    async def _capture_mtop_pc_search(self, response: Any) -> None:
        """仅保留含 resultList 的 idlemtopsearch.pc.search 响应，供列表解析。"""
        try:
            if response.status != 200:
                return
            url = str(response.url or "")
            # 与 h5api 路径、api 参数两种形态兼容
            if "idlemtopsearch" not in url.lower() or "pc.search" not in url.lower():
                return
            body = await response.json()
        except Exception:
            return
        if not isinstance(body, dict):
            return
        if not _mtop_ret_indicates_success(body):
            return
        data = body.get("data")
        if not isinstance(data, dict) or "resultList" not in data:
            return
        self._mtop_search_payload = body

    async def _fetch_pc_search_mtop(self, keyword: str) -> bool:
        """
        使用当前 Cookie 主动请求 mtop.taobao.idlemtopsearch.pc.search（sortField=create 最新优先），
        成功则写入 _mtop_search_payload。
        """
        body = await request_pc_search_list(self._context, keyword, self._log_step)
        if not isinstance(body, dict):
            return False
        self._mtop_search_payload = body
        return True

    async def __aenter__(self) -> "GoofishWebClient":
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            py = sys.executable
            raise RuntimeError(
                "当前 Python 环境未安装 playwright 包。请在「运行本服务的同一解释器」下执行：\n"
                "  %s -m pip install \"playwright>=1.40,<2\"\n"
                "  %s -m playwright install chromium\n"
                "若使用虚拟环境，请先 source .venv/bin/activate 再执行上述命令。"
                % (py, py)
            ) from e
        base = (getattr(settings, "goofish_base_url", None) or "https://www.goofish.com").rstrip("/")
        headless = bool(getattr(settings, "playwright_headless", True))
        storage = (getattr(settings, "web_storage_state_path", None) or "").strip()
        curl_file = (getattr(settings, "web_curl_cookie_file", None) or "").strip()
        self._pw = await async_playwright().start()
        # 降低被识别为自动化的概率（不保证绕过风控）
        try:
            self._browser = await self._pw.chromium.launch(
                headless=headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
        except Exception as e:
            err = (str(e) or "").lower()
            py = sys.executable
            if any(x in err for x in ("executable", "browser", "chromium", "does not exist", "no such file")):
                raise RuntimeError(
                    "Playwright 未下载 Chromium 浏览器。请执行：\n"
                    "  %s -m playwright install chromium\n"
                    "若在国内网络较慢，可多试几次或使用代理。"
                    % py
                ) from e
            raise
        ua = (getattr(settings, "playwright_user_agent", None) or "").strip() or _DEFAULT_GOOFISH_UA
        ctx_kw: Dict[str, Any] = {
            "locale": "zh-CN",
            "viewport": {"width": 1365, "height": 900},
            "user_agent": ua,
        }
        rt_state = get_playwright_storage_state()
        if rt_state is not None:
            ctx_kw["storage_state"] = rt_state
            n_rt = len(rt_state.get("cookies") or [])
            self._log_step("INFO", "已使用任务页下发的网页登录态（内存 Cookie %s 条，优先于配置文件）", n_rt)
        elif storage and Path(storage).is_file():
            ctx_kw["storage_state"] = storage
            self._log_step("INFO", "已加载网页登录态: %s", storage)
        elif curl_file and Path(curl_file).is_file():
            try:
                ctx_kw["storage_state"] = load_storage_state_from_curl_file(Path(curl_file))
                self._log_step("INFO", "已从 curl 文件解析并注入 Cookie: %s", curl_file)
            except Exception as e:
                self._log_step(
                    "WARNING",
                    "WEB_CURL_COOKIE_FILE 解析失败: %s；将无登录 Cookie",
                    str(e),
                )
        else:
            self._log_step(
                "WARNING",
                "未配置网页登录：可在任务页粘贴 cURL，或设置 WEB_STORAGE_STATE_PATH / WEB_CURL_COOKIE_FILE",
            )
        self._context = await self._browser.new_context(**ctx_kw)
        self._page = await self._context.new_page()
        await self._page.add_init_script(_GOOFISH_STEALTH_JS)
        self._mtop_search_payload = None
        self._page.on("response", self._schedule_mtop_capture)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            if self._context:
                await self._context.close()
        finally:
            try:
                if self._browser:
                    await self._browser.close()
            finally:
                if self._pw:
                    await self._pw.stop()

    async def open_search(self, keyword: str) -> None:
        """打开关键词搜索页（goofish /search?q=）。"""
        base = (getattr(settings, "goofish_base_url", None) or "https://www.goofish.com").rstrip("/")
        url = "%s/search?q=%s" % (base, quote(keyword))
        self._mtop_search_payload = None
        self._log_step("INFO", "打开搜索页 %s", url)
        await self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass
        await self._page.wait_for_timeout(1500)
        # 整页反爬提示时列表与 MTOP 均无数据，提前说明便于排查
        try:
            head = await self._page.evaluate(
                "() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 600) : ''"
            )
            if "非法访问" in head and "正常浏览器" in head:
                self._log_step(
                    "ERROR",
                    "页面返回「非法访问」反爬提示：请配置 WEB_STORAGE_STATE_PATH / WEB_CURL_COOKIE_FILE，"
                    "或设置 PLAYWRIGHT_USER_AGENT；仍失败可尝试 playwright_headless=false",
                )
        except Exception:
            pass
        # 与 h5api 一致主动拉 pc.search（按发布时间倒序），不依赖首屏拦截或点击排序
        if await self._fetch_pc_search_mtop(keyword):
            self._log_step("INFO", "已通过 MTOP(pc.search) 主动请求加载最新排序列表")
        elif not await self.wait_for_mtop_search_data(18000):
            self._log_step(
                "WARNING",
                "主动 MTOP 失败且超时未捕获页面 pc.search（可能未登录、缺 _m_h5_tk 或风控），将尝试 DOM 解析",
            )
            try:
                tail = await self._page.evaluate(
                    "() => (document.body && document.body.innerText) ? document.body.innerText.slice(0, 1200) : ''"
                )
                if "加载中" in tail:
                    self._log_step(
                        "WARNING",
                        "列表仍显示「加载中」：多为未登录或接口限流，请配置登录 Cookie（WEB_STORAGE_STATE_PATH / WEB_CURL_COOKIE_FILE）后重试",
                    )
            except Exception:
                pass

    async def click_sort_latest_if_present(self) -> None:
        """若存在「最新」排序则点击，便于接近 App 新发逻辑（页面无则跳过）。"""
        for label in ("最新", "新发", "时间"):
            try:
                loc = self._page.get_by_text(label, exact=True).first
                if loc and await loc.count() > 0:
                    self._mtop_search_payload = None
                    await loc.click(timeout=3000)
                    await self._page.wait_for_timeout(400)
                    if await self.wait_for_mtop_search_data(15000):
                        self._log_step("INFO", "已点击排序「%s」并更新 MTOP 列表数据", label)
                    else:
                        self._log_step("WARNING", "已点击排序「%s」但未等到新的 MTOP 响应", label)
                    return
            except Exception:
                continue

    async def list_search_items(self, limit: int = 8, search_keyword: str = "") -> List[Dict[str, Any]]:
        """
        在当前搜索页解析商品：优先使用页面触发的 MTOP「pc.search」JSON（与点「最新」同源）；
        未捕获到接口时再回退 DOM 锚点解析。
        search_keyword 用于 DOM 路径下弱化误抓的长文案。
        """
        await self._page.evaluate("window.scrollTo(0, Math.min(600, document.body.scrollHeight || 600))")
        await self._page.wait_for_timeout(800)
        # 首轮偶现接口晚于 goto 返回，略等以便监听器写入
        if self._mtop_search_payload is None:
            await self._page.wait_for_timeout(500)

        pl = self._mtop_search_payload
        if pl is not None:
            data = pl.get("data") or {}
            if isinstance(data, dict) and "resultList" in data:
                rl = data.get("resultList")
                api_items = parse_pc_search_items(pl, limit, search_keyword)
                if api_items:
                    self._log_step("INFO", "已从 MTOP(pc.search) 解析 %s 条商品", len(api_items))
                    return api_items
                if isinstance(rl, list) and len(rl) == 0:
                    self._log_step("INFO", "MTOP(pc.search) resultList 为空，无商品")
                    return []
                if isinstance(rl, list) and len(rl) > 0:
                    self._log_step(
                        "WARNING",
                        "MTOP 返回 %s 条卡片但解析为 0 条，回退 DOM（可能字段结构变更）",
                        len(rl),
                    )

        # 只认商品详情 href；文案仅用链接自身 innerText/title，避免向上取到整页导航
        js = """
        (lim) => {
          function isItemHref(href) {
            if (!href || typeof href !== 'string') return false;
            const h = href.split('#')[0];
            if (/javascript:|^mailto:/i.test(h)) return false;
            if (/\\/(login|im)(\\?|\\/|$)/i.test(h)) return false;
            if (/\\/search\\?/i.test(h) && !/\\/item/i.test(h)) return false;
            if (/\\/item\\?[^#]*\\bid=/i.test(h)) return true;
            if (/item\\.htm\\?[^#]*id=/i.test(h)) return true;
            if (/\\/idlefish\\/.*\\/item/i.test(h)) return true;
            if (/m\\.goofish\\.com\\/.{0,40}item/i.test(h) && /[?&]id=/i.test(h)) return true;
            if (/\\/item\\/[A-Za-z0-9_-]{8,}\\b/i.test(h)) return true;
            if (/goofish\\.com\\/.+\\bid=\\d{8,}/i.test(h)) return true;
            if (/[?&]id=\\d{10,}/i.test(h) && /goofish|idlefish|taobao/i.test(h)) return true;
            return false;
          }
          const out = [];
          const seen = new Set();
          const anchors = document.querySelectorAll('a[href]');
          for (const a of anchors) {
            const href = (a.href || '').trim();
            if (!href || seen.has(href) || !isItemHref(href)) continue;
            seen.add(href);
            let text = (a.innerText || '').replace(/\\s+/g, ' ').trim();
            if (text.length > 220) text = text.slice(0, 220);
            if (text.length < 2) {
              text = ((a.getAttribute('title') || a.getAttribute('aria-label') || '') + '').trim();
            }
            out.push({ href, text: text.slice(0, 500) });
            if (out.length >= lim * 3) break;
          }
          return out;
        }
        """
        try:
            raw: List[dict] = await self._page.evaluate(js, max(limit * 3, 24))
        except Exception as e:
            self._log_step("WARNING", "解析列表脚本异常: %s", e)
            return []
        kw = (search_keyword or "").strip()
        items: List[Dict[str, Any]] = []
        for row in raw or []:
            href = row.get("href") or ""
            text = (row.get("text") or "").strip()
            if _is_nav_or_category_blob(text):
                continue
            if kw and len(text) > 50 and kw not in text and _parse_price_from_text(text) is None:
                # 长标题且不含搜索词也无价签，多半是误抓模块文案
                continue
            price = _parse_price_from_text(text)
            items.append({"href": href, "description": text, "price": price, "list_index": len(items)})
            if len(items) >= limit:
                break
        if not items and raw:
            self._log_step(
                "WARNING",
                "页面有链接但过滤后无有效商品（可能列表为动态渲染或选择器需调整），原始候选约 %s 条",
                len(raw),
            )
        return items

    async def goto_item_and_try_buy(self, href: str) -> Tuple[bool, str]:
        """先按官网流程请求 MTOP(pc.detail)，再进入商品页并尝试点击购买。"""
        if not href:
            return False, "无链接"
        iid = _item_id_from_href(href)
        if iid:
            detail = await request_pc_item_detail(self._context, iid, self._log_step)
            if detail is not None:
                self._log_step("INFO", "已预请求 MTOP(pc.detail) itemId=%s，随后打开详情页", iid)
            else:
                self._log_step("WARNING", "MTOP(pc.detail) 失败，仍将打开详情页尝试抢购")
        await self._page.goto(href, wait_until="domcontentloaded", timeout=45000)
        await self._page.wait_for_timeout(1200)
        candidates = ("立即购买", "马上购买", "我想要", "立即下单", "领券购买")
        for name in candidates:
            try:
                btn = self._page.get_by_role("button", name=name)
                if await btn.count() > 0:
                    await btn.first.click(timeout=5000)
                    return True, "已点击「%s」" % name
            except Exception:
                continue
            try:
                link = self._page.get_by_text(name, exact=False).first
                if link and await link.count() > 0:
                    await link.click(timeout=5000)
                    return True, "已点击文案「%s」" % name
            except Exception:
                continue
        return False, "未找到购买入口（可能需登录或页面结构已变）"

    async def reload_search(self, keyword: str) -> None:
        """刷新当前搜索（与首次打开相同）。"""
        await self.open_search(keyword)
