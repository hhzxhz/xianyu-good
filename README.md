# 闲鱼抢购后台（xianyu-good）

多手机接入、按关键词自动抢购闲鱼新发商品，并统计「自己抢到」与「被他人抢走」的后台服务。

---

## 一、使用文档

### 1.1 环境要求

- **Python**：3.10 / 3.11 / 3.12（不建议 3.14，pydantic-core 无预编译包）
- **系统**：macOS / Linux（需 ADB 连接 Android 手机）
- **手机**：Android，开启 USB 调试，首次使用需安装 uiautomator2 的 atx-agent

### 1.2 安装

```bash
# 克隆或进入项目目录
cd xianyu-good

# 创建虚拟环境（Mac 若默认 python3 为 3.14，请改用 3.12：python3.12 -m venv .venv）
python3.12 -m venv .venv

# 安装依赖（须为 3.10–3.12，勿用 3.14：pydantic-core 可能无 wheel、需本机 Rust/clang）
.venv/bin/pip install -r requirements.txt

# 闲鱼网页任务：安装 Chromium（仅需一次）
.venv/bin/python -m playwright install chromium
```

若出现 **`No module named playwright`**：说明当前用的不是装过依赖的解释器，请始终用 **`项目目录/.venv/bin/python`** 启动服务（`./run.sh` 会自动用它）。

若 **`.venv/bin/pip: bad interpreter`**：虚拟环境是从别的机器/路径拷来的，请删除 `.venv` 后在本机按上面步骤重建。

### 1.3 手机准备

1. 手机开启 **开发者选项** → **USB 调试**，用数据线连接电脑。
2. 首次使用 uiautomator2 时，在电脑执行一次（会往手机装 atx-agent）：
   ```bash
   .venv/bin/python -m uiautomator2 init
   ```
3. 保持闲鱼 App 已安装并可正常打开。

### 1.4 启动服务

**不要使用 sudo。**

```bash
# 推荐：使用脚本（会自动用 .venv 并设置 PYTHONPATH）
sh ./run.sh
# 或
./run.sh
```

或直接指定端口与解释器：

```bash
.venv/bin/python main.py
# 指定端口
PORT=8002 .venv/bin/python main.py
```

启动成功后访问：

- **Web 入口说明页**：<http://127.0.0.1:8000/web>（功能说明与跳转后台）
- **后台管理页**：<http://127.0.0.1:8000/admin>（手机管理、任务管理、统计）

### C-S 架构与 Mac 客户端

- **服务端（S）**：仍在 **运行 `run.sh` / `main.py` 的机器** 上提供 REST + 静态页；负责数据库、任务轮询、ADB、Playwright 等。**抢购逻辑与数据均在服务端**，客户端只操作界面。
- **客户端（C）**：目录 `clients/mac-electron`，为 **Electron 壳 + 本地加载 `admin.html`**，所有 API 请求发往你在菜单里配置的 **服务端根地址**（如 `http://192.168.1.10:8000`）。管理页通过 URL 参数 `?api=` 与 `localStorage` 中的 `xianyu_api_base` 指向该地址。
- **本机浏览器直接用 `/admin`** 时无需配置，仍与以前一样同域访问。

**Mac 上安装/开发客户端：**

```bash
cd clients/mac-electron
npm install
npm start
```

菜单 **鲸吸购 → 服务端地址…** 填写实际服务 URL。打包 **安装程序**（需已安装 Node.js，建议 18+）：

```bash
cd clients/mac-electron
npm install
npm run build:installer
```

产物在 **`clients/mac-electron/dist/`**：

- **`.dmg`**：双击挂载，将应用拖入「应用程序」（最常用）
- **`.pkg`**：按向导安装
- **`.zip`**：解压即用

说明见 **`clients/mac-electron/README.md`**（未签名 Mac 的打开方式、仅打 dmg/pkg 等）。

每次打包或 `npm start` 前会执行 `sync-static`，从 `app/static` 同步到客户端 `static/`。

### 闲鱼网页通道（goofish，无 USB）

1. 安装浏览器内核：`pip install playwright` 后执行 `playwright install chromium`。
2. **登录态（任选其一）**
   - **A. 导出 Playwright 状态（含 localStorage）**：`python scripts/save_goofish_state.py`，在打开的浏览器中登录后回车，生成 `goofish_state.json`。`.env` 中设置 `WEB_STORAGE_STATE_PATH=./goofish_state.json`。
   - **B. 从「复制为 cURL」自动注入 Cookie**：在 Chrome 开发者工具 → Network 里对任意 `goofish.com` / `m.goofish.com` 请求右键 **Copy → Copy as cURL**，把**整段** curl 原样粘贴到项目根目录一个文本文件（如 `goofish.curl.txt`）。`.env` 中设置 `WEB_CURL_COOKIE_FILE=./goofish.curl.txt`。若同时配置了 `WEB_STORAGE_STATE_PATH` 且文件存在，则**优先用 JSON 登录态**。
   - 可选：将 curl 文件转为仅含 Cookie 的 JSON：`python scripts/curl_to_goofish_state.py goofish.curl.txt`，再使用 `WEB_STORAGE_STATE_PATH` 指向生成的 `goofish_state.json`。
3. 按需设置 `PLAYWRIGHT_HEADLESS=false` 便于首次调试。
4. 打开后台 **手机管理** → 点 **创建/获取网页环境**，新建任务时 **通道** 选 **闲鱼网页** 并选择该环境。

网页版依赖站点与接口，若 goofish 改版需调整 `app/core/xianyu_web.py`；自动下单受登录态与风控影响，不保证成功率。仅 Cookie 时若 MTOP 仍报登录异常，请改用 **A** 导出完整 `storage_state`。
- 接口文档：<http://127.0.0.1:8000/docs>
- 根路径：<http://127.0.0.1:8000/>

### 1.5 配置

| 方式 | 说明 |
|------|------|
| 环境变量 | `PORT`、`HOST`、`DATABASE_URL` 等，见 `config.py` 中 `Settings` |
| `.env` 文件 | 在项目根目录建 `.env`，键值同环境变量 |

常用项：

- `port`：服务端口，默认 8000
- `database_url`：数据库连接，默认 `sqlite+aiosqlite:///./xianyu_good.db`
- `poll_interval`：抢购轮询间隔（秒）
- `search_cooldown`：单次搜索后等待刷新时间（秒）

### 1.6 API 使用说明

#### 手机管理（/phones）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /phones | 已接入手机列表 |
| GET | /phones/adb | 当前 USB 连接的设备（接入前先查） |
| POST | /phones | 接入新手机，body: `{"device_id": "序列号", "nickname": "可选备注"}` |
| PATCH | /phones/{id} | 修改备注或启用状态，body: `{"nickname": "?", "is_active": true/false}` |
| DELETE | /phones/{id} | 移除设备（会级联删除其任务） |

#### 抢购任务（/tasks）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /tasks | 任务列表 |
| POST | /tasks | 新建任务，body: `{"phone_id": 1, "keyword": "关键词", "max_price": 可选, "min_price": 可选}` |
| PATCH | /tasks/{id} | 修改任务或启停，body: `{"keyword": "?", "max_price": ?, "min_price": ?, "is_running": true/false}` |
| DELETE | /tasks/{id} | 删除任务（运行中会先停止） |

#### 统计（/stats）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /stats/summary | 汇总：自己抢到 / 被抢走 / 总数，可选 `?task_id=` 按任务筛选 |
| GET | /stats/records | 记录列表，可选 `?task_id=&status=grabbed_by_me|grabbed_by_other&limit=` |

### 1.7 典型流程

1. **接入手机**：`GET /phones/adb` 看当前设备 → `POST /phones` 用返回的 `serial` 作为 `device_id` 接入。
2. **建任务**：`POST /tasks` 指定 `phone_id`、`keyword`、可选 `max_price`/`min_price`。
3. **开抢**：`PATCH /tasks/{id}` 传 `{"is_running": true}` 开始轮询抢购。
4. **看统计**：`GET /stats/summary`、`GET /stats/records` 查看抢到/被抢记录。

### 1.8 常见问题

- **缺少 pkg_resources**：服务已改为启动时不依赖；若在「列设备/连设备」时报错，执行  
  `.venv/bin/pip install --force-reinstall setuptools` 后重启。
- **端口被占用**：改 `PORT=8002` 再启动，或关闭占用 8000 的进程。
- **不要用 sudo 运行**：用当前用户执行 `./run.sh` 即可。

---

## 二、技术设计文档

### 2.1 架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                      FastAPI 后台 (uvicorn)                   │
│  /phones  /tasks  /stats  (CORS 开放，异步 DB)                │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  SQLite       │   │  抢购任务循环   │   │  ADB + u2     │
│  (异步)       │   │  (asyncio)     │   │  多设备连接    │
└───────────────┘   └───────┬───────┘   └───────┬───────┘
                            │                   │
                            └─────────┬─────────┘
                                      ▼
                            ┌───────────────┐
                            │  闲鱼 App 自动化 │
                            │  (u2 控件/坐标)  │
                            └───────────────┘
```

- **Web 层**：FastAPI，提供设备、任务、统计的 REST API。
- **业务层**：任务启停、抢购轮询（关键词搜索 → 新商品检测 → 价格筛选 → 点击购买 → 记录统计）。
- **设备层**：ADB 发现设备，uiautomator2 控制闲鱼 App（启动、搜索、列表、下单）。
- **持久化**：SQLite（异步 aiosqlite），存设备、任务、商品记录。

### 2.2 目录结构

```
xianyu-good/
├── README.md           # 本文档
├── config.py           # 配置（端口、DB、轮询间隔、闲鱼包名）
├── main.py             # 进程入口，启动 uvicorn
├── requirements.txt    # Python 依赖
├── run.sh              # 启动脚本（venv + PYTHONPATH）
└── app/
    ├── main.py         # FastAPI 应用、路由注册、lifespan（建表、恢复任务）
    ├── api/            # 路由
    │   ├── phones.py   # 手机 CRUD、/phones/adb
    │   ├── tasks.py    # 任务 CRUD、启停（_running 任务表）
    │   └── stats.py    # /stats/summary、/stats/records
    ├── core/            # 核心逻辑
    │   ├── device.py   # ADB 设备列表、u2 连接（adbutils/u2 延迟导入）
    │   ├── xianyu.py   # 闲鱼自动化封装（搜索、列表解析、点击购买）
    │   └── buyer.py    # 抢购循环：轮询任务、新商品判定、价格筛选、下单落库
    ├── db/
    │   ├── database.py # 异步引擎、AsyncSession、get_db、init_db
    │   └── models.py   # Phone、Task、ItemRecord、GrabStatus
    └── schemas/
        └── schemas.py  # 请求/响应 Pydantic 模型
```

### 2.3 数据模型

- **Phone**：设备表。`device_id`（ADB serial）、`nickname`、`is_active`。
- **Task**：任务表。`phone_id`、`keyword`、`max_price`/`min_price`、`is_running`。
- **ItemRecord**：商品记录表。`task_id`、`item_id`、`title`、`price`、`status`（`grabbed_by_me` / `grabbed_by_other`）、`created_at`。

关系：Phone 1:N Task，Task 1:N ItemRecord；删除 Phone 级联删除其 Task，删除 Task 级联删除其 ItemRecord。

### 2.4 抢购流程（run_task_loop）

1. 根据 `task_id` 取 Task、Phone，校验 `is_running`、`is_active`。
2. 用 `connect_device(phone.device_id)` 连 u2；失败则 sleep 后重试。
3. 启动闲鱼 → 搜索 `task.keyword` → 等待 `search_cooldown`。
4. 解析当前列表为新商品（简易 hash 去重），按 `min_price`/`max_price` 过滤。
5. 对符合条件的新品：点击进入详情 → 点「立即购买/我想要」→ 写入 ItemRecord（成功为 `grabbed_by_me`，否则 `grabbed_by_other`）→ 返回列表。
6. 无符合条件则 sleep `poll_interval`，循环。

任务启停：`PATCH /tasks/{id}` 设 `is_running` 时，后台创建或取消 `run_task_loop` 的 asyncio.Task；服务启动时 lifespan 会恢复所有 `is_running=True` 的任务。

### 2.5 技术选型说明

| 组件 | 选型 | 说明 |
|------|------|------|
| Web | FastAPI + Uvicorn | 异步、自动 OpenAPI 文档 |
| DB | SQLAlchemy 2.0 + aiosqlite | 异步 SQLite，轻量无独立服务 |
| 配置 | pydantic-settings | 环境变量 + .env |
| Android 控制 | uiautomator2 + adbutils | 基于 atx-agent，无需 Appium 重量栈 |
| 依赖隔离 | adbutils/u2 延迟导入 | 避免启动时 pkg_resources 导致服务起不来 |

### 2.6 扩展与注意

- 闲鱼界面变更时，需在 `app/core/xianyu.py` 中调整选择器（如 `description`、`text`、`resourceId`）。
- 新商品判定当前为列表文本 hash；若有接口或稳定 item_id，可改为按真实 id 去重。
- 多任务可绑定不同 Phone，实现多机同时抢不同关键词。
