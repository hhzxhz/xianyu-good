# -*- coding: utf-8 -*-
"""闲鱼 PC 商品详情：h5api mtop.taobao.idle.pc.detail（与浏览器复制 cURL 一致）。"""

from typing import Any, Optional

from app.core.goofish_mtop_common import LogCb, log_safe, mtop_request_with_data

_MTOP_API = "mtop.taobao.idle.pc.detail"
_MTOP_PATH = "https://h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail/1.0/"


async def request_pc_item_detail(context: Any, item_id: str, log_cb: LogCb = None) -> Optional[dict]:
    """
    主动请求 pc.detail，与进入详情页前浏览器发起的 MTOP 一致；用于预热会话/校验商品。

    :param context: Playwright BrowserContext
    :param item_id: 商品数字 ID
    :param log_cb: 日志回调
    :return: 成功时 MTOP 根 JSON，失败 None
    """
    iid = (item_id or "").strip()
    if not iid.isdigit():
        log_safe(log_cb, "WARNING", "MTOP pc.detail itemId 无效: %s", iid[:32])
        return None
    data_obj = {"itemId": iid}
    return await mtop_request_with_data(
        context,
        _MTOP_PATH,
        _MTOP_API,
        "a21ybx.item.0.0",
        data_obj,
        log_cb,
        "pc.detail",
    )
