# -*- coding: utf-8 -*-
"""从浏览器复制的 curl 命令中提取 Cookie，生成 Playwright storage_state（cookies 部分）。"""

import re
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import unquote


def extract_cookie_header_from_curl(curl_text: str) -> Optional[str]:
    """
    从整段 curl 文本中提取 Cookie 串（不含「Cookie:」前缀）。

    支持：-b / --cookie 的单引号或双引号参数，以及 -H 'Cookie: ...'。

    :param curl_text: 用户从开发者工具复制的完整 curl
    :return: 形如 a=1; b=2 的字符串，未找到则 None
    """
    t = re.sub(r"\\\r?\n", " ", curl_text)
    t = t.replace("\r\n", " ").replace("\n", " ")

    m = re.search(r"-b\s+'([^']*)'", t)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r'-b\s+"((?:\\.|[^"\\])*)"', t)
    if m and m.group(1).strip():
        return _unescape_curl_double_quoted(m.group(1))

    m = re.search(r"--cookie\s+'([^']*)'", t)
    if m and m.group(1).strip():
        return m.group(1).strip()
    m = re.search(r'--cookie\s+"((?:\\.|[^"\\])*)"', t)
    if m and m.group(1).strip():
        return _unescape_curl_double_quoted(m.group(1))

    for m in re.finditer(r"-H\s+'([^']*)'", t):
        h = m.group(1).strip()
        if h.lower().startswith("cookie:"):
            return h[7:].strip()
    for m in re.finditer(r'-H\s+"((?:\\.|[^"\\])*)"', t):
        h = _unescape_curl_double_quoted(m.group(1)).strip()
        if h.lower().startswith("cookie:"):
            return h[7:].strip()

    return None


def _unescape_curl_double_quoted(s: str) -> str:
    """还原 curl 双引号内常见转义。"""
    return s.replace('\\"', '"').replace("\\\\", "\\")


def parse_cookie_header_pairs(cookie_header: str) -> List[Tuple[str, str]]:
    """
    将「a=b; c=d」拆成 name-value；value 做 URL 解码以还原 %2F 等。

    :param cookie_header: Cookie 请求头内容
    :return: (name, value) 列表
    """
    out: List[Tuple[str, str]] = []
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, val = part.partition("=")
        name = name.strip()
        val = val.strip()
        if not name:
            continue
        try:
            val = unquote(val)
        except Exception:
            pass
        out.append((name, val))
    return out


def pairs_to_playwright_storage_state(
    pairs: List[Tuple[str, str]],
    domain: str,
) -> dict:
    """
    将 Cookie 对转为 Playwright new_context(storage_state=...) 所需结构。

    :param pairs: parse_cookie_header_pairs 的结果
    :param domain: 写入的 cookie domain，如 .goofish.com
    :return: 含 cookies、origins 的字典
    """
    cookies = []
    for name, value in pairs:
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "Lax",
            }
        )
    return {"cookies": cookies, "origins": []}


def infer_cookie_domain_from_curl(curl_text: str) -> str:
    """
    从 curl 请求 URL 推断一级域，用于 Set-Cookie 域；缺省为 .goofish.com。

    :param curl_text: 完整 curl
    :return: 带点前缀的 domain，如 .goofish.com
    """
    m = re.search(r"curl\s+['\"]?(https?://)([^/'\"]+)", curl_text, re.I)
    if not m:
        return ".goofish.com"
    host = m.group(2).lower().split(":")[0]
    parts = host.split(".")
    if len(parts) >= 2:
        return ".%s.%s" % (parts[-2], parts[-1])
    return ".goofish.com"


def curl_text_to_playwright_storage_state(curl_text: str) -> dict:
    """
    从整段 curl 生成 Playwright storage_state（仅 cookies）。

    :param curl_text: 开发者工具复制的 curl
    :return: storage_state 字典
    :raises ValueError: 未解析到任何 Cookie
    """
    header = extract_cookie_header_from_curl(curl_text)
    if not header:
        raise ValueError("curl 中未找到 -b/--cookie 或 Cookie 请求头")
    pairs = parse_cookie_header_pairs(header)
    if not pairs:
        raise ValueError("Cookie 串为空或无法解析")
    domain = infer_cookie_domain_from_curl(curl_text)
    return pairs_to_playwright_storage_state(pairs, domain)


def load_storage_state_from_curl_file(path: Path) -> dict:
    """
    读取文件中的 curl 全文并转换为 storage_state。

    :param path: 文本文件路径
    :return: Playwright storage_state 字典
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    return curl_text_to_playwright_storage_state(text)
