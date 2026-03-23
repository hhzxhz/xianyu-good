# -*- coding: utf-8 -*-
"""网页通道：从 cURL 提取 Cookie 并写入进程内会话，供抢购任务实时使用。"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.db.models import User
from app.schemas.schemas import WebCurlApplyReq
from app.core import web_runtime_session as wrs

router = APIRouter(prefix="/web-session", tags=["web-session"])


@router.post("/from-curl")
async def apply_curl_login(
    body: WebCurlApplyReq,
    current: User = Depends(get_current_user),
):
    """
    解析粘贴的 cURL，提取 Cookie 写入内存；正在运行的网页抢购任务会在下一轮刷新前重启浏览器以应用。
    """
    r = wrs.set_from_curl_text(body.curl_text)
    if not r.get("ok"):
        raise HTTPException(status_code=400, detail=r.get("detail") or "解析失败")
    return {
        "ok": True,
        "cookie_count": r["cookie_count"],
        "domain": r["domain"],
        "version": r["version"],
    }


@router.post("/clear")
async def clear_curl_login(current: User = Depends(get_current_user)):
    """清除任务页下发的内存登录态。"""
    return wrs.clear_runtime_session()


@router.get("/status")
async def web_session_status(current: User = Depends(get_current_user)):
    """当前内存登录态概况（不含敏感值）。"""
    return wrs.get_status_for_api()
