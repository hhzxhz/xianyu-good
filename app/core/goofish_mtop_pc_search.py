# -*- coding: utf-8 -*-
"""闲鱼 PC 搜索：h5api mtop.taobao.idlemtopsearch.pc.search 主动请求。"""

from typing import Any, Dict, Optional

from app.core.goofish_mtop_common import LogCb, log_safe, mtop_request_with_data

_MTOP_API = "mtop.taobao.idlemtopsearch.pc.search"
_MTOP_PATH = "https://h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search/1.0/"


def build_pc_search_data(keyword: str, rows_per_page: int = 30) -> Dict[str, Any]:
    """
    构造 pc.search 的 data JSON 对象（按发布时间倒序）。

    :param keyword: 搜索词
    :param rows_per_page: 每页条数（接口上限约 30）
    :return: 可 json.dumps 的字典
    """
    return {
        "pageNumber": 1,
        "keyword": keyword,
        "fromFilter": False,
        "rowsPerPage": max(1, min(30, rows_per_page)),
        "sortValue": "desc",
        "sortField": "create",
        "customDistance": "",
        "gps": "",
        "propValueStr": {"searchFilter": ""},
        "customGps": "",
        "searchReqFromPage": "pcSearch",
        "extraFilterValue": "{}",
        "userPositionJson": "{}",
    }


async def request_pc_search_list(context: Any, keyword: str, log_cb: LogCb = None) -> Optional[dict]:
    """
    使用当前 BrowserContext 的 Cookie 调用 pc.search，返回完整 MTOP JSON（含 data.resultList）。

    :param context: Playwright BrowserContext
    :param keyword: 搜索关键词
    :param log_cb: 可选日志回调 (level, msg, *args)
    :return: 成功时根对象 dict，失败 None
    """
    kw = (keyword or "").strip()
    if not kw:
        log_safe(log_cb, "WARNING", "MTOP 搜索词为空，跳过主动请求")
        return None
    data_obj = build_pc_search_data(kw, 30)
    body = await mtop_request_with_data(
        context,
        _MTOP_PATH,
        _MTOP_API,
        "a21ybx.search.0.0",
        data_obj,
        log_cb,
        "pc.search",
    )
    if not isinstance(body, dict):
        return None
    data = body.get("data")
    if not isinstance(data, dict) or "resultList" not in data:
        log_safe(log_cb, "WARNING", "MTOP pc.search 返回无 resultList")
        return None
    return body
