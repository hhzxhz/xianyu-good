# -*- coding: utf-8 -*-
"""任务页粘贴的 cURL 登录态：内存保存，供 Playwright 实时使用（优先于 .env 文件）。"""

import threading
from typing import Any, Dict, List, Optional

_lock = threading.Lock()
_storage: Optional[Dict[str, Any]] = None
_version: int = 0
_last_domain: str = ""


def get_session_version() -> int:
    """
    返回当前登录态版本号；每次成功应用或清除 cURL 时递增，供网页任务循环检测是否需重启浏览器。

    :return: 非负整数版本
    """
    with _lock:
        return _version


def get_playwright_storage_state() -> Optional[Dict[str, Any]]:
    """
    供 GoofishWebClient 读取；与配置文件互斥优先级由调用方决定。

    :return: Playwright storage_state 字典，未设置时 None
    """
    with _lock:
        return _storage


def set_from_curl_text(curl_text: str) -> Dict[str, Any]:
    """
    解析整段「复制为 cURL」文本，写入内存并 bump 版本。

    :param curl_text: 浏览器开发者工具复制的 curl 全文
    :return: 含 ok、cookie_count、domain、version 或 ok=False 与 detail
    """
    global _storage, _version, _last_domain
    from app.core.goofish_curl_cookies import curl_text_to_playwright_storage_state, infer_cookie_domain_from_curl

    text = (curl_text or "").strip()
    if not text:
        return {"ok": False, "detail": "curl 内容为空"}
    try:
        state = curl_text_to_playwright_storage_state(text)
    except ValueError as e:
        return {"ok": False, "detail": str(e)}
    n = len(state.get("cookies") or [])
    if n == 0:
        return {"ok": False, "detail": "未解析出任何 Cookie"}
    domain = infer_cookie_domain_from_curl(text)
    with _lock:
        _storage = state
        _version += 1
        _last_domain = domain
        ver = _version
    return {"ok": True, "cookie_count": n, "domain": domain, "version": ver}


def clear_runtime_session() -> Dict[str, Any]:
    """
    清除内存中的 cURL 登录态并 bump 版本，使运行中的网页任务下轮重启后回退到配置文件或无 Cookie。

    :return: 含 ok、version
    """
    global _storage, _version
    with _lock:
        _storage = None
        _version += 1
        ver = _version
    return {"ok": True, "version": ver}


def get_status_for_api() -> Dict[str, Any]:
    """
    供管理接口返回（不含 Cookie 值，仅统计与示例名）。

    :return: active、cookie_count、version、domain、sample_cookie_names
    """
    with _lock:
        cookies: List[dict] = list(_storage.get("cookies") or []) if _storage else []
        names = [str(c.get("name") or "") for c in cookies[:16] if c.get("name")]
        return {
            "active": _storage is not None,
            "cookie_count": len(cookies),
            "version": _version,
            "domain": (_last_domain or None) if _storage else None,
            "sample_cookie_names": names,
        }
