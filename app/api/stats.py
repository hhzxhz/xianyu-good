# -*- coding: utf-8 -*-
"""统计：自己抢到的商品 / 被他人抢走的商品；按当前用户任务隔离"""

from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, case, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.database import get_db
from app.db.models import ItemRecord, ParsedSearchItem, Task, Phone, GrabStatus, User
from app.schemas.schemas import ItemRecordResp, ParsedSearchItemResp, ParsedListResp, RecordsListResp, StatsResp
from app.api.auth import get_current_user

router = APIRouter(prefix="/stats", tags=["stats"])


async def _visible_task_ids(db: AsyncSession, user: User) -> set[int] | None:
    """当前用户可见的任务 id 集合；管理员返回 None 表示不限制"""
    if user.is_admin:
        return None
    r = await db.execute(select(Task.id).join(Phone, Task.phone_id == Phone.id).where(Phone.user_id == user.id))
    return {row[0] for row in r.all()}


@router.get("/summary", response_model=StatsResp)
async def stats_summary(
    task_id: Optional[int] = Query(None, description="按任务筛选，不传则全局"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """汇总：自己抢到数、被抢走数、总数（仅当前用户任务）"""
    visible = await _visible_task_ids(db, current)
    q = select(
        func.sum(case((ItemRecord.status == GrabStatus.GRABBED_BY_ME, 1), else_=0)).label("me"),
        func.sum(case((ItemRecord.status == GrabStatus.GRABBED_BY_OTHER, 1), else_=0)).label("other"),
        func.count(ItemRecord.id).label("total"),
    ).select_from(ItemRecord)
    if task_id is not None:
        if visible is not None and task_id not in visible:
            raise HTTPException(status_code=403, detail="无权限查看该任务统计")
        q = q.where(ItemRecord.task_id == task_id)
    elif visible is not None:
        q = q.where(ItemRecord.task_id.in_(visible))
    r = await db.execute(q)
    row = r.one()
    return StatsResp(
        grabbed_by_me=int(row.me or 0),
        grabbed_by_other=int(row.other or 0),
        total=int(row.total or 0),
    )


@router.get("/records", response_model=RecordsListResp)
async def list_records(
    task_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None, description="grabbed_by_me / grabbed_by_other"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(10, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """记录列表：分页，仅当前用户任务；可按任务、状态筛选"""
    visible = await _visible_task_ids(db, current)
    q_count = select(func.count(ItemRecord.id)).select_from(ItemRecord)
    q = select(ItemRecord).options(selectinload(ItemRecord.task)).order_by(ItemRecord.created_at.desc())
    if task_id is not None:
        if visible is not None and task_id not in visible:
            raise HTTPException(status_code=403, detail="无权限查看该任务")
        q_count = q_count.where(ItemRecord.task_id == task_id)
        q = q.where(ItemRecord.task_id == task_id)
    elif visible is not None:
        q_count = q_count.where(ItemRecord.task_id.in_(visible))
        q = q.where(ItemRecord.task_id.in_(visible))
    if status == "grabbed_by_me":
        q_count = q_count.where(ItemRecord.status == GrabStatus.GRABBED_BY_ME)
        q = q.where(ItemRecord.status == GrabStatus.GRABBED_BY_ME)
    elif status == "grabbed_by_other":
        q_count = q_count.where(ItemRecord.status == GrabStatus.GRABBED_BY_OTHER)
        q = q.where(ItemRecord.status == GrabStatus.GRABBED_BY_OTHER)
    total = (await db.execute(q_count)).scalar() or 0
    q = q.offset((page - 1) * page_size).limit(page_size)
    r = await db.execute(q)
    rows = r.scalars().all()
    items = [
        ItemRecordResp(
            id=x.id,
            task_id=x.task_id,
            task_name=(x.task.name if x.task else None) or "",
            item_id=x.item_id,
            title=x.title or "",
            price=x.price,
            status=x.status.value,
            created_at=x.created_at,
        )
        for x in rows
    ]
    return RecordsListResp(items=items, total=total, page=page, page_size=page_size)


@router.delete("/records/{record_id}")
async def delete_record(
    record_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """单条删除抢购记录（仅当前用户任务）"""
    r = await db.execute(select(ItemRecord).where(ItemRecord.id == record_id))
    rec = r.scalar_one_or_none()
    if not rec:
        raise HTTPException(status_code=404, detail="记录不存在")
    visible = await _visible_task_ids(db, current)
    if visible is not None and rec.task_id not in visible:
        raise HTTPException(status_code=403, detail="无权限操作")
    await db.execute(delete(ItemRecord).where(ItemRecord.id == record_id))
    await db.commit()
    return None


@router.delete("/records")
async def delete_records_batch(
    ids: str = Query(..., description="逗号分隔的记录 id，如 1,2,3"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """批量删除抢购记录（仅当前用户任务内的记录）"""
    id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    visible = await _visible_task_ids(db, current)
    if visible is not None:
        r = await db.execute(select(ItemRecord.id, ItemRecord.task_id).where(ItemRecord.id.in_(id_list)))
        for row in r.all():
            if row[1] not in visible:
                raise HTTPException(status_code=403, detail="无权限删除部分记录")
    r = await db.execute(delete(ItemRecord).where(ItemRecord.id.in_(id_list)))
    await db.commit()
    return {"deleted": r.rowcount}


@router.get("/parsed", response_model=ParsedListResp)
async def list_parsed(
    task_id: Optional[int] = Query(None, description="按任务筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页条数"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """解析到的商品列表，仅当前用户任务；分页、可按任务筛选"""
    visible = await _visible_task_ids(db, current)
    if task_id is not None:
        if visible is not None and task_id not in visible:
            raise HTTPException(status_code=403, detail="无权限查看该任务")
    cnt_q = select(func.count(ParsedSearchItem.id)).select_from(ParsedSearchItem)
    if task_id is not None:
        cnt_q = cnt_q.where(ParsedSearchItem.task_id == task_id)
    elif visible is not None:
        cnt_q = cnt_q.where(ParsedSearchItem.task_id.in_(visible))
    total = (await db.execute(cnt_q)).scalar() or 0
    q = (
        select(ParsedSearchItem)
        .options(selectinload(ParsedSearchItem.task))
        .order_by(ParsedSearchItem.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if task_id is not None:
        q = q.where(ParsedSearchItem.task_id == task_id)
    elif visible is not None:
        q = q.where(ParsedSearchItem.task_id.in_(visible))
    r = await db.execute(q)
    rows = r.scalars().all()
    items = [
        ParsedSearchItemResp(
            id=x.id,
            task_id=x.task_id,
            task_name=(x.task.name if x.task else None) or "",
            description=x.description or "",
            price=x.price,
            created_at=x.created_at,
        )
        for x in rows
    ]
    return ParsedListResp(items=items, total=total, page=page, page_size=page_size)


@router.delete("/parsed/{item_id}")
async def delete_parsed(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """单条删除解析到的商品记录（仅当前用户任务）"""
    r = await db.execute(select(ParsedSearchItem).where(ParsedSearchItem.id == item_id))
    item = r.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="记录不存在")
    visible = await _visible_task_ids(db, current)
    if visible is not None and item.task_id not in visible:
        raise HTTPException(status_code=403, detail="无权限操作")
    await db.execute(delete(ParsedSearchItem).where(ParsedSearchItem.id == item_id))
    await db.commit()
    return None


@router.delete("/parsed")
async def delete_parsed_batch(
    ids: str = Query(..., description="逗号分隔的 id，如 1,2,3"),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """批量删除解析到的商品记录（仅当前用户任务内）"""
    id_list = [int(x.strip()) for x in ids.split(",") if x.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="ids 不能为空")
    visible = await _visible_task_ids(db, current)
    if visible is not None:
        r = await db.execute(select(ParsedSearchItem.id, ParsedSearchItem.task_id).where(ParsedSearchItem.id.in_(id_list)))
        for row in r.all():
            if row[1] not in visible:
                raise HTTPException(status_code=403, detail="无权限删除部分记录")
    r = await db.execute(delete(ParsedSearchItem).where(ParsedSearchItem.id.in_(id_list)))
    await db.commit()
    return {"deleted": r.rowcount}
