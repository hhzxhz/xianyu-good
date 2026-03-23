# -*- coding: utf-8 -*-
"""认证：验证码发送、登录（验证码/密码）、当前用户、个人资料"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.db.database import get_db
from app.db.models import User
from app.schemas.schemas import (
    SendCodeReq,
    LoginCodeReq,
    LoginPwdReq,
    LoginResp,
    UserResp,
    UserUpdateMe,
)
from app.core.auth import (
    set_verification_code,
    verify_code,
    verify_password,
    hash_password,
    create_access_token,
    decode_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """从 Authorization: Bearer <token> 解析 JWT 并加载用户；未登录或已封禁则 401"""
    if not credentials or credentials.credentials is None:
        raise HTTPException(status_code=401, detail="未登录")
    payload = decode_token(credentials.credentials)
    if not payload or "sub" not in payload:
        raise HTTPException(status_code=401, detail="登录已过期或无效")
    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="无效 token")
    r = await db.execute(select(User).where(User.id == user_id))
    user = r.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="用户不存在")
    if not user.is_active:
        raise HTTPException(status_code=401, detail="账号已封禁")
    return user


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


@router.post("/send-code")
async def send_code(body: SendCodeReq):
    """发送验证码（当前为内存存储，开发环境返回 code 便于调试）"""
    code = set_verification_code(body.phone_or_email)
    # 生产环境应调用短信/邮件服务，此处仅返回开发用
    return {"message": "验证码已发送", "code": code}


@router.post("/login/code", response_model=LoginResp)
async def login_by_code(body: LoginCodeReq, db: AsyncSession = Depends(get_db)):
    """验证码登录：手机/邮箱 + 验证码，无则自动注册"""
    if not verify_code(body.phone_or_email, body.code):
        raise HTTPException(status_code=400, detail="验证码错误或已过期")
    from app.core.auth import _normalize_phone_email
    key = _normalize_phone_email(body.phone_or_email)
    is_email = "@" in key
    r = await db.execute(
        select(User).where(User.email == key if is_email else User.phone == key)
    )
    user = r.scalar_one_or_none()
    if not user:
        user = User(
            phone=key if not is_email else None,
            email=key if is_email else None,
            is_active=True,
            is_admin=False,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
    if not user.is_active:
        raise HTTPException(status_code=400, detail="账号已封禁")
    token = create_access_token(user.id, user.is_admin)
    return LoginResp(access_token=token, user=_user_to_resp(user))


@router.post("/login/pwd", response_model=LoginResp)
async def login_by_password(body: LoginPwdReq, db: AsyncSession = Depends(get_db)):
    """管理员用户名+密码登录；若库中无 admin 则自动创建（兜底未执行 seed 的情况）"""
    username = (body.username or "").strip()
    password = (body.password or "").strip()
    if not username or not password:
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    r = await db.execute(select(User).where(User.username == username))
    user = r.scalar_one_or_none()
    # 未查到 admin 时兜底创建默认管理员，避免未执行 seed 或 DB 未初始化
    if not user and username == "admin":
        user = User(
            username="admin",
            password_hash=hash_password("admin"),
            is_admin=True,
            is_active=True,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=400, detail="用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="账号已封禁")
    token = create_access_token(user.id, user.is_admin)
    return LoginResp(access_token=token, user=_user_to_resp(user))


@router.get("/me", response_model=UserResp)
async def get_me(current: User = Depends(get_current_user)):
    """当前登录用户信息"""
    return _user_to_resp(current)


@router.put("/me", response_model=UserResp)
async def update_me(
    body: UserUpdateMe,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """修改当前用户手机/邮箱"""
    if body.phone is not None:
        current.phone = (body.phone or "").strip() or None
    if body.email is not None:
        current.email = (body.email or "").strip() or None
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="该手机号或邮箱已被其他用户使用")
    await db.refresh(current)
    return _user_to_resp(current)
