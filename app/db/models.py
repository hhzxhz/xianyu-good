# -*- coding: utf-8 -*-
"""数据表：用户、手机设备、抢购任务、商品记录、统计"""

from datetime import datetime
from sqlalchemy import String, Integer, Float, DateTime, Text, Boolean, ForeignKey, Column, Enum as SQLEnum
from sqlalchemy.orm import relationship
import enum

from app.db.database import Base


class User(Base):
    """用户：验证码登录或管理员账号"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=True, comment="管理员用户名，唯一")
    password_hash = Column(String(128), nullable=True, comment="仅管理员使用")
    phone = Column(String(32), unique=True, nullable=True, index=True, comment="手机号")
    email = Column(String(128), unique=True, nullable=True, index=True, comment="邮箱")
    is_active = Column(Boolean, default=True, comment="是否启用，封禁时 False")
    is_admin = Column(Boolean, default=False, comment="是否管理员")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    phones = relationship("Phone", back_populates="user", cascade="all, delete-orphan")


class GrabStatus(str, enum.Enum):
    """抢购结果：自己抢到 / 锁定商品（待确认）/ 被他人抢走"""
    GRABBED_BY_ME = "grabbed_by_me"
    LOCKED_ITEM = "locked_item"
    GRABBED_BY_OTHER = "grabbed_by_other"


class Phone(Base):
    """已接入的手机设备，归属用户"""
    __tablename__ = "phones"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True, comment="归属用户，空表示迁移前数据")
    device_id = Column(String(64), nullable=False, comment="ADB device serial")
    nickname = Column(String(64), default="", comment="备注名")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = relationship("User", back_populates="phones")
    tasks = relationship("Task", back_populates="phone", cascade="all, delete-orphan")


class Task(Base):
    """抢购任务：搜索关键词 + 描述关键字 + 价格区间，符合则直接抢购"""
    __tablename__ = "tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    phone_id = Column(Integer, ForeignKey("phones.id"), nullable=False)
    name = Column(String(128), default="", comment="任务名称，便于区分")
    keyword = Column(String(128), nullable=False, comment="搜索关键词")
    refresh_interval_sec = Column(Integer, default=3, comment="新发页列表刷新间隔(秒)")
    run_mode = Column(String(32), default="from_app", comment="from_app=从App开始定位; new_drop_only=仅在新发/降价循环中运行")
    channel = Column(String(16), default="app", comment="app=手机App(u2); web=闲鱼网页(goofish)+Playwright")
    description_keyword = Column(String(256), default=None, comment="商品描述须包含该关键字，空表示不限制")
    max_price = Column(Float, default=None, comment="最高价，空表示不限制")
    min_price = Column(Float, default=None, comment="最低价，空表示不限制")
    is_running = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    phone = relationship("Phone", back_populates="tasks")
    records = relationship("ItemRecord", back_populates="task", cascade="all, delete-orphan")
    # order_by 用字符串延迟求值，避免 SQLAlchemy 2 在 mapper 配置时解析失败
    rules = relationship("TaskRule", back_populates="task", cascade="all, delete-orphan", order_by="TaskRule.position, TaskRule.id")


class TaskRule(Base):
    """抢购条件：多条规则间为或，满足任一条即抢；每条内描述关键字为且、价格在区间内"""
    __tablename__ = "task_rules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    position = Column(Integer, default=0, comment="排序序号，小者优先")
    description_keyword = Column(String(256), default="", comment="逗号分隔，须全部包含（且）")
    min_price = Column(Float, default=None, comment="最低价，空表示不限制")
    max_price = Column(Float, default=None, comment="最高价，空表示不限制")

    task = relationship("Task", back_populates="rules")


class ItemRecord(Base):
    """商品出现记录：用于统计「自己抢到」与「被他人抢走」"""
    __tablename__ = "item_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    item_id = Column(String(128), nullable=False, comment="闲鱼商品 ID")
    title = Column(String(512), default="")
    price = Column(Float, default=None)
    # 抢购结果（SQLite 用 VARCHAR 存枚举值，便于扩展 locked_item）
    status = Column(SQLEnum(GrabStatus, native_enum=False, length=32), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task", back_populates="records")


class ParsedSearchItem(Base):
    """新发页解析到的商品快照：商品描述、价格；item_key 用于同商品不重复保存"""
    __tablename__ = "parsed_search_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("tasks.id"), nullable=False)
    item_key = Column(String(64), default="", comment="description+price 的哈希，用于去重")
    user = Column(String(128), default="", comment="已废弃，保留列兼容")
    description = Column(String(512), default="", comment="商品描述")
    price = Column(Float, default=None, comment="价格")
    created_at = Column(DateTime, default=datetime.utcnow)

    task = relationship("Task")
