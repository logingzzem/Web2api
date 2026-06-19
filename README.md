# Web2API

使用 Python + browser-use + Playwright 实现会员网站表单上传的 API 接口服务。

外部系统通过 HTTP 调用本服务 → 服务内部启动无头浏览器 → 自动登录会员网站 → 填写并提交表单 → 返回执行结果。

---

## 技术栈

| 层级 | 选型 | 作用 |
|---|---|---|
| API 框架 | FastAPI | 提供 REST 接口 |
| 浏览器自动化 | browser-use | Agent 封装，负责登录→填表→提交流程 |
| 浏览器引擎 | Playwright (chromium) | 驱动无头浏览器 |
| 异步任务 | FastAPI BackgroundTasks | 后台执行浏览器操作，避免 HTTP 超时 |
| 任务状态 | 内存 dict | 存储任务状态（pending/running/success/failed） |
| 配置 | pydantic-settings + .env | 管理 URL、账号、字段映射等 |
| 截图存储 | 系统临时目录 (tempfile) | 保存执行过程截图 |

---

## 目录结构

```
web2api/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI 入口，路由定义
│   ├── config.py             # 配置：URL、账号、字段映射
│   ├── schemas.py            # Pydantic 请求/响应模型
│   ├── tasks.py              # 内存任务存储与状态管理
│   └── browser/
│       ├── __init__.py
│       ├── agent.py          # browser-use Agent 核心逻辑
│       └── utils.py          # 工具函数（截图、选择器等）
├── tests/
│   └── test_api.py           # 单元/集成测试
├── .env.example               # 配置示例（复制为 .env 后填写）
├── requirements.txt
├── pyproject.toml             # ruff / mypy 配置
└── README.md
```

---

## API 接口

### POST /api/v1/upload

提交表单任务（异步执行）。

**请求体：**

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

**响应：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "message": "任务已提交，正在排队执行",
  "created_at": "2026-06-19T10:00:00Z"
}
```

### GET /api/v1/tasks/{task_id}

查询任务状态。

**响应（pending）：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending",
  "result": null,
  "created_at": "2026-06-19T10:00:00Z",
  "updated_at": "2026-06-19T10:00:00Z"
}
```

**响应（success）：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "success",
  "result": {
    "message": "表单提交成功",
    "screenshot_path": "/tmp/web2api/a1b2c3d4_success.png"
  },
  "created_at": "2026-06-19T10:00:00Z",
  "updated_at": "2026-06-19T10:01:30Z"
}
```

**响应（failed）：**

```json
{
  "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "failed",
  "result": {
    "message": "登录失败：账号或密码错误",
    "screenshot_path": "/tmp/web2api/a1b2c3d4_failed.png",
    "error": "LoginFailed"
  },
  "created_at": "2026-06-19T10:00:00Z",
  "updated_at": "2026-06-19T10:00:45Z"
}
```

### GET /api/v1/tasks

查询所有任务（分页）。

**响应：**

```json
{
  "total": 10,
  "tasks": [...]
}
```

---

## 配置说明

复制 `.env.example` 为 `.env`，填写以下字段：

```env
# 会员网站 URL
LOGIN_URL=https://example.com/login
FORM_URL=https://example.com/form/upload

# 登录账号
MEMBER_USERNAME=your_username
MEMBER_PASSWORD=your_password

# 表单字段映射（API字段名 -> 页面选择器）
# 格式：FORM_FIELD_<API_FIELD_NAME>=CSS选择器
FORM_FIELD_MEMBER_NAME=#member_name
FORM_FIELD_PHONE=input[name="phone"]
FORM_FIELD_EMAIL=#email
FORM_FIELD_REMARK=textarea[name="remark"]

# 提交按钮选择器
SUBMIT_BUTTON_SELECTOR=button[type="submit"]

# 浏览器配置
BROWSER_HEADLESS=true
BROWSER_TIMEOUT_MS=30000

# 截图保存目录
SCREENSHOT_DIR=/tmp/web2api
```

### 表单字段映射规则

在 `.env` 中以 `FORM_FIELD_` 前缀定义映射：

```
FORM_FIELD_<API字段名大写>=<CSS选择器或name属性>
```

例如 API 传入 `"member_name": "张三"`，配置 `FORM_FIELD_MEMBER_NAME=#member_name`，则 Agent 会执行 `page.fill('#member_name', '张三')`。

---

## 快速启动

### 1. 安装依赖

```bash
pip install -r requirements.txt
playwright install chromium
```

### 2. 配置

```bash
cp .env.example .env
# 编辑 .env，填写 URL、账号、字段映射
```

### 3. 启动服务

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 4. 调用示例

```bash
curl -X POST http://localhost:8000/api/v1/upload \
  -H "Content-Type: application/json" \
  -d '{
    "form_data": {
      "member_name": "张三",
      "phone": "13800138000",
      "email": "zhang@example.com"
    }
  }'
```

---

## 浏览器自动化流程

```
1. 启动 Playwright（chromium，无头模式）
2. 打开 LOGIN_URL
3. 填写登录表单（username + password）
4. 点击登录按钮，等待页面跳转
5. 打开 FORM_URL
6. 遍历 form_data，按字段映射填写每个字段
7. 点击提交按钮（SUBMIT_BUTTON_SELECTOR）
8. 等待响应，截取最终页面截图
9. 提取页面结果信息（成功/失败提示）
10. 更新任务状态，关闭浏览器
```

---

## 错误处理

| 错误码 | 说明 |
|---|---|
| `LoginFailed` | 登录失败（账号/密码错误，或页面元素找不到） |
| `FormFillError` | 表单字段填写失败（选择器未找到） |
| `SubmitError` | 提交按钮点击失败 |
| `NavigationError` | 页面跳转/导航失败 |
| `TimeoutError` | 浏览器操作超时 |

---

## 开发指南

### 代码风格

使用 ruff 进行格式化与 lint 检查：

```bash
ruff check .
ruff format .
```

类型检查：

```bash
mypy app/
```

### 运行测试

```bash
pytest tests/ -v
```

> 测试文件在验证完成后会删除。

---

## 注意事项

- 本服务需要在能访问目标会员网站的网络环境中运行。
- 账号密码明文写在 `.env` 中，生产环境请使用密钥管理服务。
- 截图保存在系统临时目录，酌情清理。
- 任务状态存在内存中，服务重启后会丢失。如需持久化可改为 Redis。
