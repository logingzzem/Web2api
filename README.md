# Web2API

使用 Python + browser-use + Playwright 实现会员网站表单上传的 API 接口服务。

外部系统通过 HTTP 调用本服务 → 服务内部启动无头浏览器 → 自动登录 → 按文字智能点击导航按钮 → 填写并提交表单 → 返回执行结果。

---

## 技术栈

| 层级 | 选型 | 作用 |
|---|---|---|
| API 框架 | FastAPI | 提供 REST 接口 |
| 浏览器自动化 | Playwright (chromium) | 驱动浏览器执行登录 → 点击 → 填表 → 提交 |
| 智能定位 | 多策略查找 | role=button/link、文本匹配、label、placeholder、aria-label |
| 异步任务 | FastAPI BackgroundTasks | 后台执行浏览器操作，避免 HTTP 超时 |
| 任务状态 | 内存 dict | 存储 pending/running/success/failed |
| 配置 | pydantic-settings + .env | 管理 URL、账号、字段映射、导航步骤 |
| 截图保存 | 系统临时目录 (tempfile/web2api) | 保存成功/失败截图 |

---

## 目录结构

```
web2api/
├── app/
│   ├── main.py              # FastAPI 入口，路由定义
│   ├── config.py             # 配置：URL、账号、导航步骤、字段映射
│   ├── schemas.py            # Pydantic 请求/响应模型
│   ├── tasks.py              # 内存任务存储与状态管理
│   └── browser/
│       ├── agent.py          # 浏览器 Agent：登录 → 导航 → 填表 → 提交
│       └── utils.py          # 工具函数（截图、消息提取等）
├── .env.example               # 配置模板
├── requirements.txt
└── README.md
```

---

## 浏览器自动化流程

```
① 打开 LOGIN_URL → 填写用户名/密码 → 点击登录
② 进入 LANDING_URL → 按 NAVIGATION_STEPS 列表 *依次* 查找并点击文字按钮
   （如果未配置 NAVIGATION_STEPS，则直接 goto FORM_URL）
③ 等待 FORM_READY_SELECTOR 出现（如弹窗 / 表单容器）
④ 遍历 form_data 填表：
   策略 A：FORM_FIELD_<字段名> 的 CSS 选择器 → 优先
   策略 B：get_by_label(<字段名>) 自动匹配
   策略 C：placeholder 含字段名匹配
⑤ 点击提交按钮：
   策略 A：SUBMIT_BUTTON_SELECTOR 的 CSS 选择器
   策略 B：SUBMIT_BUTTON_TEXTS 列表的文本匹配（提交/保存/确认/...）
⑥ 等待网络空闲 → 截图 → 提取结果消息
```

---

## 配置说明（核心：NAVIGATION_STEPS + FORM_FIELD_ 映射）

复制 `.env.example` 为 `.env`，重点配置：

```env
LOGIN_URL=https://example.com/login
LANDING_URL=https://example.com/member           # 登录后落地页（从这里开始点按钮）

# ★ 方式 A（推荐）：按按钮文字自动查找并点击，支持多步
# 英文逗号分隔，例如：会员管理 → 添加 → 新增表单
NAVIGATION_STEPS=会员管理,添加,新增表单

# ★ 方式 B（简单 URL 场景）：直接跳转到表单 URL
# FORM_URL=https://example.com/member/form/new

# 登录账号
MEMBER_USERNAME=your_username
MEMBER_PASSWORD=your_password

# 登录表单选择器（逗号分隔多候选，按顺序尝试）
USERNAME_SELECTOR=input[name='username'],#username,input[type='text']
PASSWORD_SELECTOR=input[name='password'],#password,input[type='password']
LOGIN_BUTTON_SELECTOR=button[type='submit'],input[type='submit'],.login-btn

# 表单字段映射（字段名大写，FORM_FIELD_ 前缀）
# 配置了会优先使用，未配置则通过 label/placeholder 智能匹配
FORM_FIELD_MEMBER_NAME=#member_name
FORM_FIELD_PHONE=input[name="phone"]
FORM_FIELD_EMAIL=#email
FORM_FIELD_REMARK=textarea[name="remark"]

# 提交按钮（先 CSS 选择器，找不到再按文本匹配）
SUBMIT_BUTTON_SELECTOR=button[type='submit'],input[type='submit']
SUBMIT_BUTTON_TEXTS=提交,保存,确认,Submit,Save,Confirm

# 弹窗场景的表单就绪标志（留空则不额外等待）
FORM_READY_SELECTOR=.modal-dialog,.ant-modal

# 浏览器配置
BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=30000

# 截图保存目录（留空则使用系统临时目录下的 web2api 子目录）
SCREENSHOT_DIR=
```

### 智能查找策略优先级（`_find_and_click`）

对每个目标文字（如"添加"），按以下顺序尝试：

| # | 策略 | 说明 |
|---|---|---|
| 1 | `get_by_role("button", name="添加")` | 最可靠，只匹配按钮 |
| 2 | `get_by_role("link", name="添加")` | 匹配 `<a>` 链接按钮，用于菜单导航 |
| 3 | `locator(":scope").filter(has_text="添加")` | 可见文本元素里找 |
| 4 | `[value*='添加'] / [title*='添加']` | 按表单 value/tooltip 文本 |
| 5 | `[aria-label*='添加']` | 无障碍标签 |

---

## API 接口

### POST /api/v1/upload

提交表单任务（异步执行）。

请求体：
```json
{
  "form_data": {
    "member_name": "张三",
    "phone": "13800138000",
    "email": "zhang@example.com",
    "remark": "测试提交"
  },
  "callback_url": "https://your-server.com/webhook"
}
```

响应：
```json
{
  "task_id": "a1b2c3d4-...",
  "status": "pending",
  "message": "任务已提交，正在排队执行",
  "created_at": "2026-06-19T10:00:00Z"
}
```

### GET /api/v1/tasks/{task_id}

查询任务状态。
```json
{
  "task_id": "...",
  "status": "success",
  "result": {
    "message": "操作成功，已提交",
    "screenshot_path": "/tmp/web2api/..._success.png",
    "error": null
  },
  "created_at": "...",
  "updated_at": "..."
}
```

可能的状态：`pending` / `running` / `success` / `failed`。

失败时 `result.error` 字段会返回：
- `LoginFailed` — 登录失败
- `NavigationError` — 导航步骤中找不到按钮
- `FormFillError` — 表单填写异常
- `SubmitError` — 提交按钮找不到
- `UnknownError` — 其他异常

### GET /api/v1/tasks

返回全部任务，分页通过 `skip` / `limit` 参数控制。

### GET /health

健康检查。

---

## 快速启动

```bash
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
# 编辑 .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

---

## 调试建议

1. **先把 `BROWSER_HEADLESS=false`**，肉眼观察点击是否正确；
2. **把 `NAVIGATION_STEPS` 拆成一小步一小步**调试，比如第一步只配 `会员管理`，确认能点到后再加 `添加`；
3. 查看日志里的 `②-1 查找并点击: ...` 和 `④ 表单填写完成，成功 N 个，失败 N 个` 信息；
4. `screenshot_path` 截图可直接看失败时的页面状态；
5. 文本匹配不到时，配置 `FORM_READY_SELECTOR=...`（如 `.modal-dialog`）让 Agent 等待弹窗出现。
