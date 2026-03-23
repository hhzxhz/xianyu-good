# -*- coding: utf-8 -*-
"""异步数据库连接与会话管理"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
)
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)
Base = declarative_base()


async def get_db():
    """FastAPI 依赖：获取异步 DB 会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db():
    """启动时创建表，并补齐新增字段（如 description_keyword）"""
    # 确保所有 model 已注册到 Base.metadata，否则 create_all 不会建表
    import app.db.models  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite 补齐 tasks.description_keyword 列（若表已存在且无该列）
        def _add_column(connection):
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='description_keyword'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN description_keyword VARCHAR(256) DEFAULT NULL"))
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='name'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN name VARCHAR(128) DEFAULT ''"))
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('parsed_search_items') WHERE name='item_key'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE parsed_search_items ADD COLUMN item_key VARCHAR(64) DEFAULT ''"))
                connection.execute(text(
                    "UPDATE parsed_search_items SET item_key = 'legacy_' || id WHERE item_key = '' OR item_key IS NULL"
                ))
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_parsed_task_item_key ON parsed_search_items(task_id, item_key)"
                ))
            # task_rules.position：规则排序，小者优先
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('task_rules') WHERE name='position'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE task_rules ADD COLUMN position INTEGER DEFAULT 0"))
            # tasks.refresh_interval_sec：新发页刷新间隔(秒)
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='refresh_interval_sec'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN refresh_interval_sec INTEGER DEFAULT 3"))
            # tasks.run_mode：运行模式 from_app | new_drop_only
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='run_mode'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN run_mode VARCHAR(32) DEFAULT 'from_app'"))
            # tasks.channel：app | web
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('tasks') WHERE name='channel'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE tasks ADD COLUMN channel VARCHAR(16) DEFAULT 'app'"))
            # phones.user_id：归属用户
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('phones') WHERE name='user_id'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE phones ADD COLUMN user_id INTEGER DEFAULT NULL"))
            # users.updated_at：若表为旧版创建则可能缺此列，UPDATE 会报错
            c = connection.execute(text(
                "SELECT 1 FROM pragma_table_info('users') WHERE name='updated_at'"
            )).fetchone()
            if not c:
                connection.execute(text("ALTER TABLE users ADD COLUMN updated_at DATETIME DEFAULT NULL"))
        await conn.run_sync(lambda c: _add_column(c))
