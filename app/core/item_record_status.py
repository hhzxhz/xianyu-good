# -*- coding: utf-8 -*-
"""抢购记录状态：根据自动化返回值区分我抢到 / 锁定商品 / 被抢"""

from app.db.models import GrabStatus


def status_from_buy_attempt(ok: bool, msg: str) -> GrabStatus:
    """
    失败记为被抢；成功时若结果描述含锁单相关文案则记为锁定商品（可在后台「转为我抢到」），否则记为我抢到。
    """
    if not ok:
        return GrabStatus.GRABBED_BY_OTHER
    s = msg or ""
    # 与闲鱼「商品锁定」「锁单」等提示对齐，可按实际日志再扩展关键字
    if any(k in s for k in ("锁定", "锁单", "锁货", "已锁定", "锁定中")):
        return GrabStatus.LOCKED_ITEM
    return GrabStatus.GRABBED_BY_ME
