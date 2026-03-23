# -*- coding: utf-8 -*-
"""手机设备：列表、接入、编辑、删除；按当前用户隔离，管理员可见全部"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Phone, User
from app.schemas.schemas import PhoneCreate, PhoneUpdate, PhoneResp
from app.core.device import list_adb_devices
from app.api.tasks import stop_running_tasks_by_phone
from app.api.auth import get_current_user

router = APIRouter(prefix="/phones", tags=["phones"])


def _can_manage_phone(phone: Phone, user: User) -> bool:
    """当前用户是否可管理该设备（本人或管理员）"""
    return user.is_admin or (phone.user_id is not None and phone.user_id == user.id)


@router.get("", response_model=list[PhoneResp])
async def list_phones(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """已登记设备列表：普通用户仅自己的，管理员可见全部"""
    q = select(Phone).order_by(Phone.id)
    if not current.is_admin:
        q = q.where(Phone.user_id == current.id)
    r = await db.execute(q)
    return r.scalars().all()


@router.get("/adb", response_model=list[dict])
async def list_adb(_: User = Depends(get_current_user)):
    """当前通过 USB 连接的设备（供接入时选择）"""
    try:
        return list_adb_devices()
    except RuntimeError as e:
        if "pkg_resources" in str(e):
            raise HTTPException(
                status_code=503,
                detail="缺少 pkg_resources，请在本机执行: .venv/bin/pip install --force-reinstall setuptools 后重启服务",
            ) from e
        raise


@router.api_route("/web", methods=["GET", "POST"], response_model=PhoneResp)
async def ensure_web_phone(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """
    创建或返回当前用户唯一的「闲鱼网页」运行环境（device_id=web:{用户id}）。
    同时支持 GET 与 POST，避免浏览器直接访问 /phones/web 时出现 405。
    """
    device_id = "web:%s" % current.id
    r = await db.execute(select(Phone).where(Phone.device_id == device_id))
    existing = r.scalars().first()
    if existing:
        return existing
    phone = Phone(device_id=device_id, nickname="闲鱼网页", user_id=current.id)
    db.add(phone)
    await db.flush()
    await db.refresh(phone)
    return phone


@router.post("", response_model=PhoneResp)
async def add_phone(
    body: PhoneCreate,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """接入新手机：按 device_id 登记，归属当前用户"""
    r = await db.execute(select(Phone).where(Phone.device_id == body.device_id))
    if r.scalars().one_or_none():
        raise HTTPException(status_code=400, detail="该设备已接入")
    phone = Phone(
        device_id=body.device_id,
        nickname=body.nickname or "",
        user_id=current.id,
    )
    db.add(phone)
    await db.flush()
    await db.refresh(phone)
    return phone


@router.patch("/{phone_id}", response_model=PhoneResp)
async def update_phone(
    phone_id: int,
    body: PhoneUpdate,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """修改备注或启用状态（仅本人或管理员可操作）"""
    phone = await db.get(Phone, phone_id)
    if not phone:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not _can_manage_phone(phone, current):
        raise HTTPException(status_code=403, detail="无权限操作该设备")
    if body.nickname is not None:
        phone.nickname = body.nickname
    if body.is_active is not None:
        phone.is_active = body.is_active
        if body.is_active is False:
            await stop_running_tasks_by_phone(phone_id, db)
    await db.flush()
    await db.refresh(phone)
    return phone


@router.delete("/{phone_id}")
async def delete_phone(
    phone_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """移除设备（会级联删除其任务）；仅本人或管理员可操作"""
    phone = await db.get(Phone, phone_id)
    if not phone:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not _can_manage_phone(phone, current):
        raise HTTPException(status_code=403, detail="无权限操作该设备")
    await db.delete(phone)
    return {"ok": True}
