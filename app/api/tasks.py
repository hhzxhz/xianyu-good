# -*- coding: utf-8 -*-
"""抢购任务：创建、启停、列表；多线程执行，每任务一线程+独立事件循环"""

import asyncio
import threading
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import get_db
from app.db.models import Task, TaskRule, Phone, User
from app.schemas.schemas import TaskCreate, TaskUpdate, TaskResp, TaskBatchReq
from app.core.buyer import run_task_loop, get_session_factory
from app.core import task_console as console
from app.api.auth import get_current_user

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _normalize_channel(raw: Optional[str]) -> str:
    """任务通道：app 为手机自动化，web 为 goofish 网页。"""
    c = (raw or "app").strip().lower()
    return c if c in ("app", "web") else "app"


def _phone_is_web_placeholder(phone: Phone) -> bool:
    """device_id 形如 web:123 表示该「设备」走网页 Playwright，非 ADB。"""
    return (phone.device_id or "").startswith("web:")

# 当前正在运行的任务 id -> 执行该任务的线程（多线程并发，互不阻塞）
_running: dict[int, threading.Thread] = {}
_lock = threading.Lock()


def _run_loop_in_thread(task_id: int) -> None:
    """在线程内创建独立事件循环并运行任务轮询，避免 u2/ADB 阻塞主循环"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        factory = get_session_factory()
        loop.run_until_complete(run_task_loop(task_id, factory))
    finally:
        loop.close()
        with _lock:
            _running.pop(task_id, None)


def _start_task_loop(task_id: int):
    """在新线程中启动任务轮询（每任务一线程，设备操作互不阻塞）"""
    console.init_task(task_id)
    th = threading.Thread(target=_run_loop_in_thread, args=(task_id,), daemon=True, name=f"task-{task_id}")
    with _lock:
        _running[task_id] = th
    th.start()


async def stop_running_tasks_by_phone(phone_id: int, db: AsyncSession) -> list[int]:
    """停用设备时调用：将该设备下所有运行中任务停止并更新 DB，返回被停止的 task_id 列表"""
    r = await db.execute(select(Task.id).where(Task.phone_id == phone_id, Task.is_running == True))
    ids = [row[0] for row in r.all()]
    for tid in ids:
        with _lock:
            _running.pop(tid, None)
    if ids:
        await db.execute(update(Task).where(Task.phone_id == phone_id, Task.is_running == True).values(is_running=False))
    return ids


@router.get("", response_model=list[TaskResp])
async def list_tasks(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """任务列表（含规则）：普通用户仅自己设备的任务，管理员全部"""
    q = select(Task).options(selectinload(Task.rules)).order_by(Task.id)
    if not current.is_admin:
        q = q.join(Phone, Task.phone_id == Phone.id).where(Phone.user_id == current.id)
    r = await db.execute(q)
    return list(r.scalars().unique().all())


@router.get("/running", response_model=list[int])
async def list_running_task_ids(
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """当前正在执行的任务 ID 列表；仅返回当前用户可见的任务"""
    with _lock:
        running_ids = set(_running.keys())
    if current.is_admin:
        return list(running_ids)
    r = await db.execute(select(Task.id).join(Phone, Task.phone_id == Phone.id).where(Phone.user_id == current.id))
    allowed = {row[0] for row in r.all()}
    return [tid for tid in running_ids if tid in allowed]


@router.get("/console")
async def get_task_console(
    task_id: Optional[int] = Query(None, description="不传则返回所有运行中任务日志"),
    limit: int = Query(300, le=500),
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """运行中任务的控制台日志；仅可查当前用户可见任务的日志"""
    if task_id is not None:
        task = await db.get(Task, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")
        phone = await db.get(Phone, task.phone_id)
        if phone and not current.is_admin and phone.user_id != current.id:
            raise HTTPException(status_code=403, detail="无权限查看该任务")
    return {"lines": console.get_lines(task_id=task_id, limit=min(limit, 500))}


def _task_rules_from_body(rules_list):
    """从请求体 rules 生成 TaskRule 列表，按列表下标设置 position（供 add/update 用）"""
    if not rules_list:
        return []
    return [
        TaskRule(
            position=i,
            description_keyword=(r.description_keyword or "").strip() or "",
            min_price=r.min_price,
            max_price=r.max_price,
        )
        for i, r in enumerate(rules_list)
    ]


@router.post("", response_model=TaskResp)
async def create_task(
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """新建抢购任务（可带多条规则，条间或）；设备须归属当前用户"""
    phone = await db.get(Phone, body.phone_id)
    if not phone:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not current.is_admin and phone.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权限在该设备下创建任务")
    channel = _normalize_channel(body.channel)
    if channel == "web" and not _phone_is_web_placeholder(phone):
        raise HTTPException(
            status_code=400,
            detail="网页任务请选择「闲鱼网页」运行环境（手机管理页可创建），勿选 USB 手机",
        )
    if channel == "app" and _phone_is_web_placeholder(phone):
        raise HTTPException(status_code=400, detail="App 任务请选择已接入的 USB 手机设备")
    run_mode = (body.run_mode or "from_app").strip() or "from_app"
    if run_mode not in ("from_app", "new_drop_only"):
        run_mode = "from_app"
    task = Task(
        phone_id=body.phone_id,
        name=(body.name or "").strip() or "",
        keyword=body.keyword,
        refresh_interval_sec=max(1, min(60, (body.refresh_interval_sec if body.refresh_interval_sec is not None else 3))),
        channel=channel,
        run_mode=run_mode,
        description_keyword=body.description_keyword,
        max_price=body.max_price,
        min_price=body.min_price,
    )
    db.add(task)
    await db.flush()
    if body.rules:
        for tr in _task_rules_from_body(body.rules):
            tr.task_id = task.id
            db.add(tr)
    elif body.description_keyword is not None or body.min_price is not None or body.max_price is not None:
        db.add(TaskRule(task_id=task.id, description_keyword=(body.description_keyword or "") or "", min_price=body.min_price, max_price=body.max_price))
    await db.flush()
    # 重新加载 task 以带上 rules 供响应
    r = await db.execute(select(Task).options(selectinload(Task.rules)).where(Task.id == task.id))
    task = r.scalar_one()
    return task


@router.post("/batch")
async def tasks_batch(
    body: TaskBatchReq,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """批量停止或删除任务；同一请求内去重 id。"""
    action = (body.action or "").strip().lower()
    if action not in ("stop", "delete"):
        raise HTTPException(status_code=400, detail="action 须为 stop 或 delete")
    if not body.task_ids:
        raise HTTPException(status_code=400, detail="task_ids 不能为空")
    ids = list(dict.fromkeys(body.task_ids))
    errors: list[dict] = []
    n_ok = 0
    for tid in ids:
        task = await db.get(Task, tid)
        if not task:
            errors.append({"id": tid, "detail": "任务不存在"})
            continue
        phone = await db.get(Phone, task.phone_id)
        if phone and not current.is_admin and phone.user_id != current.id:
            errors.append({"id": tid, "detail": "无权限"})
            continue
        with _lock:
            _running.pop(tid, None)
        if action == "stop":
            task.is_running = False
            n_ok += 1
        else:
            await db.delete(task)
            n_ok += 1
    await db.commit()
    return {"ok": n_ok, "errors": errors}


@router.post("/{task_id}/duplicate", response_model=TaskResp)
async def duplicate_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """
    复制任务：同一设备、相同关键词/通道/规则等，新任务默认未运行（is_running=False）。
    """
    r = await db.execute(select(Task).options(selectinload(Task.rules)).where(Task.id == task_id))
    src = r.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="任务不存在")
    phone = await db.get(Phone, src.phone_id)
    if phone and not current.is_admin and phone.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权限操作该任务")
    ch = _normalize_channel(getattr(src, "channel", None))
    rm = (src.run_mode or "from_app").strip() or "from_app"
    if rm not in ("from_app", "new_drop_only"):
        rm = "from_app"
    new_task = Task(
        phone_id=src.phone_id,
        name=(src.name or "").strip(),
        keyword=src.keyword,
        refresh_interval_sec=max(1, min(60, getattr(src, "refresh_interval_sec", None) or 3)),
        channel=ch,
        run_mode=rm,
        description_keyword=src.description_keyword,
        max_price=src.max_price,
        min_price=src.min_price,
        is_running=False,
    )
    db.add(new_task)
    await db.flush()
    rules_src = list(src.rules or [])
    if rules_src:
        for tr in sorted(rules_src, key=lambda x: (x.position, x.id)):
            db.add(
                TaskRule(
                    task_id=new_task.id,
                    position=tr.position,
                    description_keyword=(tr.description_keyword or "").strip() or "",
                    min_price=tr.min_price,
                    max_price=tr.max_price,
                )
            )
    elif src.description_keyword is not None or src.min_price is not None or src.max_price is not None:
        db.add(
            TaskRule(
                task_id=new_task.id,
                position=0,
                description_keyword=(src.description_keyword or "") or "",
                min_price=src.min_price,
                max_price=src.max_price,
            )
        )
    await db.flush()
    r2 = await db.execute(select(Task).options(selectinload(Task.rules)).where(Task.id == new_task.id))
    return r2.scalar_one()


@router.patch("/{task_id}", response_model=TaskResp)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """修改任务或启停（仅任务所属设备的用户或管理员可操作）"""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    phone = await db.get(Phone, task.phone_id)
    if phone and not current.is_admin and phone.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权限操作该任务")
    if body.name is not None:
        task.name = (body.name or "").strip() or ""
    if body.keyword is not None:
        task.keyword = body.keyword
    if body.refresh_interval_sec is not None:
        task.refresh_interval_sec = max(1, min(60, body.refresh_interval_sec))
    # 通道与绑定设备须一致：可单独改通道或改设备，或同时改
    if body.phone_id is not None or body.channel is not None:
        new_phone_id = task.phone_id if body.phone_id is None else body.phone_id
        cur_ch = (getattr(task, "channel", None) or "app").strip().lower()
        new_channel = _normalize_channel(body.channel) if body.channel is not None else cur_ch
        if body.phone_id is not None:
            np = await db.get(Phone, body.phone_id)
            if not np:
                raise HTTPException(status_code=404, detail="目标设备不存在")
            if not current.is_admin and np.user_id != current.id:
                raise HTTPException(status_code=403, detail="无权限绑定该设备")
            new_phone_id = body.phone_id
        ph = await db.get(Phone, new_phone_id)
        if not ph:
            raise HTTPException(status_code=400, detail="设备不存在")
        if new_channel == "web" and not _phone_is_web_placeholder(ph):
            raise HTTPException(
                status_code=400,
                detail="网页任务须绑定闲鱼网页运行环境（手机管理页创建）",
            )
        if new_channel == "app" and _phone_is_web_placeholder(ph):
            raise HTTPException(status_code=400, detail="App 任务须选择 USB 手机设备")
        if body.phone_id is not None:
            task.phone_id = new_phone_id
        if body.channel is not None:
            task.channel = new_channel
    if body.run_mode is not None:
        task.run_mode = body.run_mode if body.run_mode in ("from_app", "new_drop_only") else "from_app"
    if body.description_keyword is not None:
        task.description_keyword = body.description_keyword
    if body.max_price is not None:
        task.max_price = body.max_price
    if body.min_price is not None:
        task.min_price = body.min_price
    if body.rules is not None:
        await db.execute(delete(TaskRule).where(TaskRule.task_id == task_id))
        for tr in _task_rules_from_body(body.rules):
            tr.task_id = task_id
            db.add(tr)
    if body.is_running is not None:
        task.is_running = body.is_running
        if task.is_running:
            # 同一手机单次只允许运行一个任务：先停止该设备上其他运行中的任务
            r_other = await db.execute(
                select(Task.id).where(Task.phone_id == task.phone_id, Task.id != task_id, Task.is_running == True)
            )
            for (other_id,) in r_other.all():
                with _lock:
                    _running.pop(other_id, None)
            await db.execute(
                update(Task).where(Task.phone_id == task.phone_id, Task.id != task_id, Task.is_running == True).values(is_running=False)
            )
            with _lock:
                should_start = task_id not in _running
            if should_start:
                await db.commit()  # 先提交再启动循环，否则后台线程用新 session 读到未提交的 is_running=False 会立刻退出
                _start_task_loop(task_id)
        else:
            with _lock:
                _running.pop(task_id, None)
            await db.commit()  # 立即提交，确保后台线程下次读 DB 能看到 is_running=False
    await db.flush()
    r = await db.execute(select(Task).options(selectinload(Task.rules)).where(Task.id == task_id))
    task = r.scalar_one()
    return task


@router.delete("/{task_id}")
async def delete_task(
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current: User = Depends(get_current_user),
):
    """删除任务（若在运行会先停止）；仅设备所属用户或管理员可操作"""
    task = await db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    phone = await db.get(Phone, task.phone_id)
    if phone and not current.is_admin and phone.user_id != current.id:
        raise HTTPException(status_code=403, detail="无权限操作该任务")
    with _lock:
        _running.pop(task_id, None)
    await db.delete(task)
    return {"ok": True}
