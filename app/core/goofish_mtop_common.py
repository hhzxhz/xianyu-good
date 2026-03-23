# -*- coding: utf-8 -*-
"""闲鱼 h5api MTOP 公共：sign、Cookie token、通用 POST。"""

import hashlib
import json
import time
from typing import Any, Callable, Dict, List, Optional

from urllib.parse import urlencode

LogCb = Optional[Callable[..., None]]

APP_KEY = "34839810"
JSV = "2.7.2"


def log_safe(cb: LogCb, level: str, msg: str, *args: Any) -> None:
    if cb:
        cb(level.upper(), msg, *args)


def token_from_m_h5_tk(cookies: List[Dict[str, Any]]) -> Optional[str]:
    """
    从 Playwright Cookie 列表取 _m_h5_tk 前半段，供 MTOP sign 使用。

    :param cookies: context.cookies() 返回值
    :return: token 字符串，缺失则 None
    """
    for c in cookies:
        if c.get("name") == "_m_h5_tk":
            val = (c.get("value") or "").strip()
            if "_" in val:
                return val.split("_", 1)[0].strip()
            return val or None
    return None


def mtop_ret_ok(body: dict) -> bool:
    """判断 MTOP 根级 ret 是否成功。"""
    ret = body.get("ret")
    if isinstance(ret, str):
        return "SUCCESS" in ret
    if isinstance(ret, list):
        return any(isinstance(x, str) and "SUCCESS" in x for x in ret)
    return False


def sign_mtop(token: str, t_ms: int, app_key: str, data_json: str) -> str:
    """Taobao MTOP 常用 sign：MD5(token&t&appKey&data)。"""
    raw = "%s&%s&%s&%s" % (token, t_ms, app_key, data_json)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def build_mtop_get_url(
    path: str,
    api: str,
    t_ms: int,
    sign: str,
    spm_cnt: str,
    extra_query: Optional[Dict[str, str]] = None,
) -> str:
    """
    拼接 MTOP GET 查询串（与浏览器复制 cURL 一致的主干参数）。

    :param path: 完整路径如 https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/
    :param api: api 参数值，如 mtop.taobao.idle.pc.detail
    :param t_ms: 毫秒时间戳
    :param sign: MD5 签名
    :param spm_cnt: spm_cnt
    :param extra_query: 可选附加参数（如 spm_pre、log_id）
    :return: 带 query 的 URL
    """
    q: Dict[str, str] = {
        "jsv": JSV,
        "appKey": APP_KEY,
        "t": str(t_ms),
        "sign": sign,
        "v": "1.0",
        "type": "originaljson",
        "accountSite": "xianyu",
        "dataType": "json",
        "timeout": "20000",
        "api": api,
        "sessionOption": "AutoLoginOnly",
        "spm_cnt": spm_cnt,
    }
    if extra_query:
        for k, v in extra_query.items():
            if v is not None and v != "":
                q[k] = v
    return path + "?" + urlencode(q)


async def post_mtop_form(
    context: Any,
    url: str,
    data_json: str,
    log_cb: LogCb,
    tag: str,
) -> Optional[dict]:
    """
    POST application/x-www-form-urlencoded，body 字段 data=JSON 字符串。

    :param context: Playwright BrowserContext
    :param url: 完整 URL（含 query）
    :param data_json: 与参与 sign 的字符串一致
    :param log_cb: 日志回调
    :param tag: 日志中的接口简称
    :return: 解析后的 JSON dict，失败 None
    """
    headers = {
        "accept": "application/json",
        "origin": "https://www.goofish.com",
        "referer": "https://www.goofish.com/",
    }
    try:
        resp = await context.request.post(url, headers=headers, form={"data": data_json})
    except Exception as e:
        log_safe(log_cb, "WARNING", "MTOP %s 请求异常: %s", tag, str(e))
        return None
    if resp.status != 200:
        log_safe(log_cb, "WARNING", "MTOP %s HTTP %s", tag, resp.status)
        return None
    try:
        body = await resp.json()
    except Exception as e:
        log_safe(log_cb, "WARNING", "MTOP %s 响应非 JSON: %s", tag, str(e))
        return None
    if not isinstance(body, dict):
        return None
    if not mtop_ret_ok(body):
        log_safe(log_cb, "WARNING", "MTOP %s 业务失败: %s", tag, str(body.get("ret"))[:200])
        return None
    return body


async def mtop_request_with_data(
    context: Any,
    path: str,
    api: str,
    spm_cnt: str,
    data_obj: dict,
    log_cb: LogCb,
    tag: str,
    extra_query: Optional[Dict[str, str]] = None,
) -> Optional[dict]:
    """
    计算 sign 并发起一次 MTOP POST（data 为 JSON 对象）。

    :param context: BrowserContext
    :param path: MTOP 接口 path（至 /1.0/）
    :param api: api 参数
    :param spm_cnt: spm_cnt
    :param data_obj: 写入 form data 的 JSON 对象
    :param log_cb: 日志
    :param tag: 日志标签
    :param extra_query: 附加 query
    :return: 成功返回根 JSON
    """
    cookies = await context.cookies()
    token = token_from_m_h5_tk(cookies)
    if not token:
        log_safe(log_cb, "WARNING", "Cookie 中无 _m_h5_tk，跳过 MTOP %s", tag)
        return None
    data_json = json.dumps(data_obj, ensure_ascii=False, separators=(",", ":"))
    t_ms = int(time.time() * 1000)
    sig = sign_mtop(token, t_ms, APP_KEY, data_json)
    url = build_mtop_get_url(path, api, t_ms, sig, spm_cnt, extra_query)
    return await post_mtop_form(context, url, data_json, log_cb, tag)
