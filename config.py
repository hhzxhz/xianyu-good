# -*- coding: utf-8 -*-
"""项目配置：数据库路径、服务端口、闲鱼包名等"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


# 闲鱼 App 包名（正式环境）
XIANYU_PACKAGE = "com.taobao.idlefish"


class Settings(BaseSettings):
    """仅从当前进程环境变量读取（export、systemd、Docker 等）；不加载项目根 .env。"""
    # 服务监听
    host: str = "0.0.0.0"
    port: int = 28080
    # 数据库
    database_url: str = "sqlite+aiosqlite:///./xianyu_good.db"
    # 抢购轮询间隔（秒）
    poll_interval: float = 3.0
    # 单次搜索后等待新商品刷新时间（秒）
    search_cooldown: float = 5.0
    # 是否将任务运行日志写入 logs/ 目录。可设 SAVE_TASK_LOG=1 开启
    save_task_log: bool = False
    # 是否在关键步骤（含解析前新发页）保存页面 UI 层级 XML 到 logs/，便于根据 dump 优化商品解析。可设 DEBUG_DUMP_UI=1 开启
    debug_dump_ui: bool = False
    # JWT 密钥（生产环境请设置环境变量）
    jwt_secret: str = "xianyu-good-secret-change-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 72
    # 闲鱼网页（goofish）自动化：Playwright 使用的登录态文件，生成方式见 README
    web_storage_state_path: str = ""
    # 将浏览器「复制为 cURL」的整段命令保存为文本文件后，配置此路径可从 -b / Cookie 头自动注入 Cookie（与 WEB_STORAGE_STATE_PATH 二选一，优先使用后者若文件存在）
    web_curl_cookie_file: str = ""
    # 是否无头运行浏览器；首次登录建议 false 在本机完成扫码/登录后导出 storage state
    playwright_headless: bool = True
    # 自定义 Playwright User-Agent；留空则使用内置桌面 Chrome UA，降低 goofish「非法访问」拦截概率
    playwright_user_agent: str = ""
    # 网页搜索基址（官方一般为 https://www.goofish.com）
    goofish_base_url: str = "https://www.goofish.com"
    # 所有 REST 接口统一挂载此前缀（须与 app/static/admin.html 内 FEISHU_API_PREFIX 一致；可通过环境变量 API_MOUNT_PREFIX 覆盖）
    api_mount_prefix: str = "/feishu-good"

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
    )


# 项目根目录，用于拼接 DB 路径
BASE_DIR = Path(__file__).resolve().parent
settings = Settings()
