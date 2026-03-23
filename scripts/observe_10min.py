# -*- coding: utf-8 -*-
"""
观察任务运行约 10 分钟：确保有设备/任务后启动 run_task_loop（在独立线程中，与 API 一致），
主线程定时将控制台日志写入文件，到点后停止任务并保存完整日志供分析。
"""
import asyncio
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

# 项目根目录
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from sqlalchemy import select, update
from app.db.database import init_db, AsyncSessionLocal
from app.db.models import User, Phone, Task, TaskRule
from app.core.device import list_adb_devices
from app.core.buyer import run_task_loop, get_session_factory
from app.core import task_console as console


OBSERVE_SECONDS = int(os.environ.get("OBSERVE_SECONDS", "600"))  # 默认 10 分钟，可环境变量覆盖
LOG_DUMP_INTERVAL = 30  # 每 30 秒把控制台日志追加到观察日志文件
OUTPUT_DIR = ROOT / "logs"
OBSERVE_LOG = OUTPUT_DIR / "observe_10min.log"


async def ensure_task_and_phone():
    """确保存在可用的 Phone 和 Task，返回 (task_id, device_serial)。"""
    devices = list_adb_devices()
    if not devices:
        raise RuntimeError("未检测到 ADB 设备，请连接手机")
    serial = devices[0]["serial"]

    await init_db()
    async with AsyncSessionLocal() as session:
        # 获取或创建用户（无则创建管理员账号供观察用）
        r = await session.execute(select(User).limit(1))
        user = r.scalar_one_or_none()
        if not user:
            user = User(username="observe", is_admin=True, is_active=True)
            session.add(user)
            await session.flush()
        # 获取或创建设备
        r = await session.execute(select(Phone).where(Phone.device_id == serial))
        phone = r.scalar_one_or_none()
        if not phone:
            phone = Phone(device_id=serial, user_id=user.id, is_active=True)
            session.add(phone)
            await session.flush()
        # 获取或创建任务（关键词用简单词便于快速进列表）
        r = await session.execute(select(Task).where(Task.phone_id == phone.id).limit(1))
        task = r.scalar_one_or_none()
        if not task:
            task = Task(
                phone_id=phone.id,
                keyword="手机",
                name="观察任务",
                refresh_interval_sec=5,
                run_mode="from_app",
                is_running=False,
            )
            session.add(task)
            await session.flush()
            session.add(TaskRule(task_id=task.id, position=0, description_keyword="", min_price=None, max_price=None))
        task_id = task.id
        await session.commit()
    return task_id, serial


async def set_task_running(task_id: int, running: bool):
    """设置任务 is_running 状态"""
    async with AsyncSessionLocal() as session:
        await session.execute(update(Task).where(Task.id == task_id).values(is_running=running))
        await session.commit()


def dump_console_to_file(task_id: int, fp, after_index: int = 0) -> int:
    """将当前任务控制台日志中 after_index 之后的新行追加写入文件，返回当前总行数"""
    lines = console.get_lines(task_id=task_id, limit=500)
    for i in range(after_index, len(lines)):
        item = lines[i]
        t = item.get("time_full") or item.get("time", "")
        level = item.get("level", "")
        msg = item.get("msg", "")
        fp.write("[%s] [%s] %s\n" % (t, level, msg))
    fp.flush()
    return len(lines)


def _run_task_in_thread(task_id: int) -> None:
    """在独立线程中运行任务循环（与 API 一致，避免同步 u2 阻塞主循环）"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(run_task_loop(task_id, get_session_factory()))
    finally:
        loop.close()


async def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    task_id, serial = await ensure_task_and_phone()
    console.init_task(task_id)
    await set_task_running(task_id, True)

    th = threading.Thread(target=_run_task_in_thread, args=(task_id,), daemon=True)
    th.start()

    with open(OBSERVE_LOG, "w", encoding="utf-8") as f:
        f.write("# 任务 %s 观察开始 @ %s (设备 %s)，计划运行 %s 秒\n" % (
            task_id, datetime.now().isoformat(), serial, OBSERVE_SECONDS))
        f.flush()
        start_mono = time.monotonic()
        last_dump = start_mono
        dump_index = 0
        try:
            while th.is_alive():
                time.sleep(5)
                now = time.monotonic()
                if now - start_mono >= OBSERVE_SECONDS:
                    break
                if now - last_dump >= LOG_DUMP_INTERVAL:
                    dump_index = dump_console_to_file(task_id, f, dump_index)
                    last_dump = now
            await set_task_running(task_id, False)
            for _ in range(60):
                if not th.is_alive():
                    break
                time.sleep(1)
        finally:
            dump_index = dump_console_to_file(task_id, f, dump_index)
            f.write("\n# 观察结束 @ %s\n" % datetime.now().isoformat())

    print("观察结束，日志已写入:", OBSERVE_LOG)
    return OBSERVE_LOG


if __name__ == "__main__":
    asyncio.run(main())
