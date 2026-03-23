# -*- coding: utf-8 -*-
"""管理员：用户列表、封禁/启用"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import UserResp, AdminUserUpdate
from app.api.auth import get_current_user

router = APIRouter(prefix="/admin", tags=["admin"])


async def get_current_admin(current: User = Depends(get_current_user)) -> User:
    """要求当前用户为管理员"""
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current


def _user_to_resp(u: User) -> UserResp:
    return UserResp(
        id=u.id,
        username=u.username,
        phone=u.phone,
        email=u.email,
        is_active=u.is_active,
        is_admin=u.is_admin,
        created_at=u.created_at,
    )


@router.get("/users", response_model=list[UserResp])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """管理员：获取所有用户列表"""
    r = await db.execute(select(User).order_by(User.id))
    return [_user_to_resp(u) for u in r.scalars().all()]


def _is_super_admin(user: User) -> bool:
    """仅用户名为 admin 的超级管理员可授权/撤销其他用户的管理员身份"""
    return bool(user.username and user.username.strip() == "admin")


@router.patch("/users/{user_id}", response_model=UserResp)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """管理员：封禁/启用、手机邮箱；仅超级管理员(admin)可修改 is_admin"""
    if user_id == admin.id and body.is_active is False:
        raise HTTPException(status_code=400, detail="不能封禁自己")
    if body.is_admin is not None and not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="仅超级管理员可授权或撤销管理员")
    if user_id == admin.id and body.is_admin is False:
        raise HTTPException(status_code=400, detail="不能撤销自己的管理员身份")
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.phone is not None:
        user.phone = (body.phone or "").strip() or None
    if body.email is not None:
        user.email = (body.email or "").strip() or None
    if body.is_admin is not None and _is_super_admin(admin):
        user.is_admin = body.is_admin
    await db.flush()
    await db.refresh(user)
    return _user_to_resp(user)
