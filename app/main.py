# -*- coding: utf-8 -*-
"""FastAPI 应用入口：注册路由、初始化 DB、启动时恢复运行中任务"""

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy import select

# 抢购任务日志输出到控制台，便于排查「任务无反应」
_log = logging.getLogger("xianyu.buyer")
if not _log.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    _log.addHandler(_h)
    _log.setLevel(logging.INFO)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db.database import init_db, AsyncSessionLocal
from app.db.models import Task
from app.api import phones, tasks, stats, notifications as notifications_api, web_session
from app.api import auth, admin_users
from app.core.auth import seed_admin_user


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时建表、种子管理员、恢复 is_running=True 的任务"""
    try:
        await init_db()
    except Exception as e:
        raise RuntimeError(f"数据库初始化失败: {e}") from e
    try:
        async with AsyncSessionLocal() as session:
            await seed_admin_user(session)
            await session.commit()
    except Exception as e:
        import traceback
        traceback.print_exc()
        _log.warning("种子管理员失败: %s", e)
    try:
        async with AsyncSessionLocal() as session:
            r = await session.execute(select(Task).where(Task.is_running == True))
            for task in r.scalars().all():
                if task.id not in tasks._running:
                    tasks._start_task_loop(task.id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        _log.warning("启动时恢复运行中任务失败: %s", e)
    yield
    # 任务在独立线程中运行（daemon），进程退出时自动结束，无需在此取消


app = FastAPI(
    title="闲鱼抢购后台",
    description="多手机接入，按关键词自动抢购新发商品，并统计抢到/被抢",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(phones.router)
app.include_router(tasks.router)
app.include_router(web_session.router)
# 与仅转发 /api/* 的反向代理兼容（管理页会探测 /web-session 与 /api/web-session）
app.include_router(web_session.router, prefix="/api")
app.include_router(stats.router)
app.include_router(notifications_api.router)

# 后台管理页：静态资源与 /admin
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/")
async def root():
    return {"service": "xianyu-good", "docs": "/docs", "admin": "/admin", "web": "/web"}


@app.get("/web")
async def web_portal():
    """Web 入口页：产品说明与进入 /admin 控制台（与静态后台能力一致）"""
    web_file = Path(__file__).resolve().parent / "static" / "web.html"
    if not web_file.is_file():
        return {"error": "web.html not found"}
    return FileResponse(
        web_file,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/admin")
async def admin_page():
    """后台管理页：手机、任务、统计。禁止缓存以确保前端不轮询逻辑始终为最新"""
    admin_file = Path(__file__).resolve().parent / "static" / "admin.html"
    if not admin_file.is_file():
        return {"error": "admin.html not found"}
    return FileResponse(
        admin_file,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
