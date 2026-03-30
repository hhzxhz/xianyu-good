# -*- coding: utf-8 -*-
"""API 请求/响应模型"""

from typing import Optional, List, Literal
from pydantic import BaseModel
from datetime import datetime


class UserResp(BaseModel):
    id: int
    username: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_active: bool
    is_admin: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SendCodeReq(BaseModel):
    phone_or_email: str


class LoginCodeReq(BaseModel):
    phone_or_email: str
    code: str


class LoginPwdReq(BaseModel):
    username: str
    password: str


class LoginResp(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResp


class UserUpdateMe(BaseModel):
    phone: Optional[str] = None
    email: Optional[str] = None


class AdminUserCreate(BaseModel):
    """管理员手动创建用户：用户名+密码登录，可选手机/邮箱"""
    username: str
    password: str
    phone: Optional[str] = None
    email: Optional[str] = None
    is_active: Optional[bool] = True
    is_admin: Optional[bool] = False


class AdminUserUpdate(BaseModel):
    """管理员修改用户：封禁/启用、手机、邮箱、重置密码；仅超级管理员可改 is_admin"""
    is_active: Optional[bool] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    is_admin: Optional[bool] = None
    password: Optional[str] = None


class TaskRuleItem(BaseModel):
    """单条规则：描述关键字（逗号分隔且）、价格区间；多条规则间为或"""
    description_keyword: Optional[str] = ""
    min_price: Optional[float] = None
    max_price: Optional[float] = None


class TaskRuleResp(BaseModel):
    id: int
    task_id: int
    description_keyword: str
    min_price: Optional[float] = None
    max_price: Optional[float] = None

    class Config:
        from_attributes = True


class PhoneCreate(BaseModel):
    device_id: str
    nickname: Optional[str] = ""


class PhoneUpdate(BaseModel):
    nickname: Optional[str] = None
    is_active: Optional[bool] = None


class PhoneResp(BaseModel):
    id: int
    device_id: str
    nickname: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TaskCreate(BaseModel):
    phone_id: int
    name: Optional[str] = ""
    keyword: str
    refresh_interval_sec: Optional[int] = 3
    channel: Optional[str] = "app"  # app | web
    run_mode: Optional[str] = "from_app"  # from_app | new_drop_only
    description_keyword: Optional[str] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    rules: Optional[List[TaskRuleItem]] = None


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    keyword: Optional[str] = None
    refresh_interval_sec: Optional[int] = None
    phone_id: Optional[int] = None
    channel: Optional[str] = None  # app | web
    run_mode: Optional[str] = None  # from_app | new_drop_only
    description_keyword: Optional[str] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    is_running: Optional[bool] = None
    rules: Optional[List[TaskRuleItem]] = None


class TaskBatchReq(BaseModel):
    """批量操作任务：停止或删除（须逐条有权限）。"""
    task_ids: list[int]
    action: str  # stop | delete


class TaskResp(BaseModel):
    id: int
    phone_id: int
    name: str
    keyword: str
    refresh_interval_sec: int = 3
    channel: str = "app"
    run_mode: str = "from_app"
    description_keyword: Optional[str] = None
    max_price: Optional[float] = None
    min_price: Optional[float] = None
    is_running: bool
    created_at: datetime
    rules: List[TaskRuleResp] = []

    class Config:
        from_attributes = True


class ItemRecordResp(BaseModel):
    id: int
    task_id: int
    task_name: Optional[str] = None
    item_id: str
    title: str
    price: Optional[float] = None
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class RecordsListResp(BaseModel):
    """抢购记录列表分页响应"""
    items: list[ItemRecordResp]
    total: int
    page: int
    page_size: int


class ParsedSearchItemResp(BaseModel):
    """解析到的商品（新发页），不包含用户列"""
    id: int
    task_id: int
    task_name: Optional[str] = None
    description: str
    price: Optional[float] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ParsedListResp(BaseModel):
    """解析商品列表分页响应"""
    items: list[ParsedSearchItemResp]
    total: int
    page: int
    page_size: int


class StatsResp(BaseModel):
    """抢购统计：自己抢到 / 锁定商品 / 被他人抢走数量"""
    grabbed_by_me: int
    locked_item: int
    grabbed_by_other: int
    total: int


class ItemRecordConfirmMeReq(BaseModel):
    """将「锁定商品」记录标记为「我抢到」"""
    status: Literal["grabbed_by_me"]


class WebCurlApplyReq(BaseModel):
    """任务页粘贴的浏览器「复制为 cURL」全文，用于提取 Cookie"""
    curl_text: str
