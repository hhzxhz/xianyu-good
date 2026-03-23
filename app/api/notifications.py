# -*- coding: utf-8 -*-
"""Web 端弹框：识别到符合条件商品时的通知接口；按当前用户任务过滤"""

from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Task, Phone, User
from app.api.auth import get_current_user
from app.core import notifications

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("/recent")
async def recent_match_notifications(
    limit: int = Query(20, ge=1, le=50, description="条数"),
    after_id: Optional[int] = Query(None, description="只返回 id 大于此值的通知，用于轮询新数据"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """最近「识别到符合条件商品」的通知，仅当前用户任务"""
    raw = notifications.get_recent(limit=limit * 2, after_id=after_id)  # 多取一些再过滤
    if current.is_admin:
        return raw[:limit]
    r = await db.execute(select(Task.id).join(Phone, Task.phone_id == Phone.id).where(Phone.user_id == current.id))
    visible = {row[0] for row in r.all()}
    out = [n for n in raw if n.get("task_id") in visible][:limit]
    return out
