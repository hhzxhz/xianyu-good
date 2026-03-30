# -*- coding: utf-8 -*-
"""管理员：用户列表、手动创建、封禁/启用、重置密码"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import UserResp, AdminUserUpdate, AdminUserCreate
from app.api.auth import get_current_user
from app.core.auth import hash_password

router = APIRouter(prefix="/admin", tags=["admin"])


async def get_current_admin(current: User = Depends(get_current_user)) -> User:
    """要求当前用户为管理员"""
    if not current.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return current


def _user_to_resp(u: User) -> UserResp:
    """将 ORM 用户转为 API 响应体。"""
    return UserResp(
        id=u.id,
        username=u.username,
        phone=u.phone,
        email=u.email,
        is_active=u.is_active,
        is_admin=u.is_admin,
        created_at=u.created_at,
    )


def _normalize_email(raw: str | None) -> str | None:
    """邮箱去空白并小写（含 @ 时）。"""
    s = (raw or "").strip() or None
    if s and "@" in s:
        return s.lower()
    return s


def _is_super_admin(user: User) -> bool:
    """仅用户名为 admin 的超级管理员可授权/撤销其他用户的管理员身份。"""
    return bool(user.username and user.username.strip() == "admin")


@router.get("/users", response_model=list[UserResp])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(get_current_admin),
):
    """管理员：获取所有用户列表"""
    r = await db.execute(select(User).order_by(User.id))
    return [_user_to_resp(u) for u in r.scalars().all()]


@router.post("/users", response_model=UserResp)
async def create_user(
    body: AdminUserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    管理员：手动创建用户（用户名+密码）；仅超级管理员可将 is_admin 设为 True。
    """
    uname = (body.username or "").strip()
    pwd = body.password or ""
    if len(uname) < 2 or len(uname) > 64:
        raise HTTPException(status_code=400, detail="用户名长度须为 2～64 字符")
    if len(pwd) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    want_admin = bool(body.is_admin)
    if want_admin and not _is_super_admin(admin):
        raise HTTPException(status_code=403, detail="仅超级管理员可创建管理员账号")
    phone = (body.phone or "").strip() or None
    email = _normalize_email(body.email)
    is_active = body.is_active if body.is_active is not None else True
    user = User(
        username=uname,
        password_hash=hash_password(pwd),
        phone=phone,
        email=email,
        is_active=is_active,
        is_admin=want_admin if _is_super_admin(admin) else False,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=400, detail="用户名、手机或邮箱已存在")
    await db.refresh(user)
    return _user_to_resp(user)


@router.patch("/users/{user_id}", response_model=UserResp)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    管理员：封禁/启用、手机邮箱、重置密码（新密码非空时写入）；
    仅超级管理员(admin)可修改 is_admin。
    """
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
        user.email = _normalize_email(body.email)
    # 显式传入 password 且非空时更新哈希；留空或不传表示不修改
    if body.password is not None:
        pwd = body.password.strip()
        if pwd:
            if len(pwd) < 6:
                raise HTTPException(status_code=400, detail="密码至少 6 位")
            user.password_hash = hash_password(pwd)
    if body.is_admin is not None and _is_super_admin(admin):
        user.is_admin = body.is_admin
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=400, detail="手机或邮箱与其他用户冲突")
    await db.refresh(user)
    return _user_to_resp(user)
