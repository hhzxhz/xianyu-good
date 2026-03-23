# -*- coding: utf-8 -*-
"""认证：密码哈希、验证码存储、JWT、默认管理员"""

import re
import time
import random
import string
from typing import Optional

from passlib.context import CryptContext
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from app.db.models import User

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 验证码内存存储：key=归一化手机/邮箱, value=(code, 过期时间戳)
_code_store: dict[str, tuple[str, float]] = {}
_CODE_TTL = 300  # 5 分钟


# bcrypt 仅支持最多 72 字节，超出会报错，统一截断
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    p = (password or "").encode("utf-8")[: _BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore")
    return pwd_ctx.hash(p)


def verify_password(plain: str, hashed: str) -> bool:
    p = (plain or "").encode("utf-8")[: _BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore")
    return pwd_ctx.verify(p, hashed)


def _normalize_phone_email(s: str) -> str:
    """归一化：去空格、小写邮箱"""
    s = (s or "").strip()
    if "@" in s:
        return s.lower()
    return re.sub(r"\s+", "", s)


def set_verification_code(phone_or_email: str) -> str:
    """生成 6 位数字验证码并写入存储，返回该码（开发环境可前端直接使用）"""
    key = _normalize_phone_email(phone_or_email)
    code = "".join(random.choices(string.digits, k=6))
    _code_store[key] = (code, time.time() + _CODE_TTL)
    return code


def verify_code(phone_or_email: str, code: str) -> bool:
    """校验验证码"""
    key = _normalize_phone_email(phone_or_email)
    if key not in _code_store:
        return False
    stored_code, expiry = _code_store[key]
    if time.time() > expiry:
        del _code_store[key]
        return False
    if stored_code != (code or "").strip():
        return False
    del _code_store[key]
    return True


def create_access_token(user_id: int, is_admin: bool = False) -> str:
    """生成 JWT"""
    from datetime import datetime, timedelta
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expire_hours)
    payload = {"sub": str(user_id), "admin": is_admin, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> Optional[dict]:
    """解析 JWT，失败返回 None"""
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


async def seed_admin_user(db: AsyncSession) -> None:
    """若不存在管理员则创建默认 admin/admin；若已存在则强制重置密码为 admin，保证可登录"""
    r = await db.execute(select(User).where(User.username == "admin"))
    user = r.scalar_one_or_none()
    if user:
        # 已存在：每次启动强制重置为 admin，避免旧哈希/编码问题导致无法登录
        user.password_hash = hash_password("admin")
        user.is_active = True
        user.is_admin = True
        await db.flush()
        return
    user = User(
        username="admin",
        password_hash=hash_password("admin"),
        is_admin=True,
        is_active=True,
    )
    db.add(user)
    await db.flush()
