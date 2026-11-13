# LLM 题库桥（llm-qbank-bridge）

把网课脚本的「搜题」请求，接到你自己的大模型 API 上。

一个 **单文件 Python 本地服务**：只监听 `127.0.0.1`，对外提供与题库接口同形的 HTTP 响应，对内把题目转发给任意 OpenAI 兼容模型（DeepSeek、通义、Kimi、硅基流动、本地 Ollama……），带本地缓存、并发控制、用量统计和一个网页控制台。

> 只在本机跑，不依赖任何第三方题库服务；答案由你自己的模型生成，费用走你自己的 API Key。

---

## 特性

- **任意 OpenAI 兼容接口**：填 `base_url` + `api_key` + `model` 即可，也支持 Dify。
- **本地答案缓存**：同一道题（题干 + 选项 + 题型）算一次，命中直接返回，省 token 也省时间。
- **用量统计**：按输入 / 输出单价折算费用，分「今日 / 累计」，可一键重置。
- **网页控制台**（`ui.html`）：改配置、看实时日志、查缓存、跑自测、看用量、清缓存。
- **访问密钥**：脚本必须带 `X-Consumer-Token` 才放行，避免本机其它程序蹭用。
- **脚本适配**：一键把通用题库脚本指到本服务（`/api/patch`），并顺带落盘一份到 `local-scripts/`。
- **自带打包脚本**：PyInstaller 打成单文件 exe（约 8.4 MB），可脱离 Python 运行。

## 目录结构

```
llm-qbank-bridge/
├── qbank_bridge.py       服务本体（单文件，只依赖标准库）
├── ui.html               控制台页面
├── start.bat             Windows 启动器（自动找可用 Python，前台运行）
├── config.example.json   配置模板（复制为 config.json 后填写）
└── qbank/                打包输出目录（git 忽略，运行时生成）
```

运行后会自动在程序目录生成：`config.json`（配置）、`cache.json`（答案缓存）、`usage.json`（用量）、`local-scripts/`（适配后的脚本）。

## 快速开始

### 方式一：直接跑源码（需要 Python 3.8+）

```bash
python qbank_bridge.py
```

Windows 下也可以双击 `start.bat`：它会自动逐个探测可用的 Python（会跳过微软商店那个假的 `python.exe`），前台运行并把日志留在窗口里，关掉窗口即停止服务。

### 方式二：单文件 exe

打包产物放在 `qbank/`，双击 `qbank.exe` 即可。首次运行会在 exe 同级目录生成 `config.json`。**不要放在 `C:\Program Files` 这类只读目录**，否则配置和缓存写不进去。

### 配置

首次启动会生成 `config.json`；也可以复制 `config.example.json` 后手动填。启动后打开控制台改更省事：

```
http://127.0.0.1:8765
```

| 字段 | 说明 |
| --- | --- |
| `provider` | `openai`（OpenAI 兼容）或 `dify` |
| `base_url` | 接口地址，如 `https://api.deepseek.com/v1` |
| `api_key` | 模型服务的 Key，只存在本机 `config.json` |
| `model` | 模型名，如 `deepseek-chat` |
| `extra_headers` | 额外请求头（部分中转需要） |
| `port` | 监听端口，默认 `8765`；被占用时自动往后找，最多试 21 个 |
| `access_token` | 本服务的访问密钥，留空会自动生成 `llm-xxxxxxxx` |
| `auth_mode` | `strict` 校验密钥；`any` 任意密钥放行（本机自用图省事） |
| `timeout` | 单题等待秒数，默认 12 |
| `temperature` / `max_tokens` | 采样参数，默认 0 / 512 |
| `workers` | 并发线程数，1~12 |
| `system_prompt` | 自定义系统提示词，留空用内置的 |
| `quota` | 费用上限（元），`0` 表示只统计不限制 |
| `price_in` / `price_out` | 输入 / 输出单价（元 / 百万 token），用于估算花费 |

## HTTP 接口

服务默认在 `http://127.0.0.1:8765`。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | 控制台页面 |
| GET | `/api/ping` | 存活探测，返回 `{"ok":true,"app":"qbank-bridge"}` |
| GET | `/api/state?since=N` | 配置、统计、用量、缓存数量、`N` 之后的日志 |
| POST | `/api/config` | 保存配置（改端口或并发数会重启监听） |
| POST | `/api/test` | 自测：用一条示例题真实调用模型 |
| POST | `/api/patch` | 脚本适配：把提交的脚本文本改指到本服务 |
| POST | `/api/cache/clear` | 清空答案缓存 |
| POST | `/api/usage/reset` | 重置用量（`{"scope":"today"}` 或 `all`） |
| POST | `/api/open/v1/search/exact` | **搜题主接口**（题库接口同形） |

### 搜题请求

```http
POST /api/open/v1/search/exact
Content-Type: application/json
X-Consumer-Token: llm-xxxxxxxx
```

```json
{
  "stem": "以下哪个是 Python 的内置数据类型？",
  "options": ["list", "vector", "array", "matrix"],
  "type": "0"
}
```

`type`：`0` 单选、`1` 多选、`3` 判断。返回结构与常见题库接口一致：

```json
{
  "success": true,
  "data": {
    "items": [{
      "stem": "……",
      "answer": { "kind": "text", "values": ["list"], "raw": "{\"answer\":[\"list\"],\"confidence\":0.98}" }
    }],
    "total": 1
  },
  "error": null,
  "meta": { "billing_result": "ok", "source": "llm", "cached": false, "elapsed_ms": 621, "confidence": 0.98 }
}
```

出错时 `success` 为 `false`，`error.code` 可能是 `AUTH_MISSING`、`AUTH_MISMATCH`、`UPSTREAM_ERROR` 等。

## 脚本适配

控制台里粘贴脚本内容，或直接调 `/api/patch`，服务会把脚本里的题库地址替换成 `http://127.0.0.1:<端口>`，并把结果同时返回和落盘到 `local-scripts/`。

- 服务**不做**任何题库抓取，只做转发 + 缓存。
- 适配后的脚本仍需你在浏览器扩展里自行安装。

## 打包

```bash
python -m venv .build/venv
.build/venv/Scripts/pip install pyinstaller
.build/venv/Scripts/python -m PyInstaller --onefile --console --name qbank ^
  --add-data "ui.html;." --optimize 2 --distpath qbank --workpath .build/work ^
  --exclude-module tkinter --exclude-module unittest --exclude-module decimal ^
  --exclude-module ctypes --exclude-module bz2 --exclude-module lzma ^
  --exclude-module xml --exclude-module pyexpat qbank_bridge.py
```

体积下限主要来自 `python3xx.dll` 与 `libssl/libcrypto`（HTTPS 必需），当前约 8.4 MB。

## 常见问题

**启动报 9009（命令找不到）**
PATH 里有个微软商店的假 `python.exe`（指向 `AppInstallerPythonRedirector.exe`）。`start.bat` 会逐个候选试跑 `python -V` 并跳过 `\WindowsApps\` 路径；也可以直接把真实路径写进脚本的 `set "PY="`。彻底解决：设置 → 应用 → 高级应用设置 → 应用执行别名 → 关掉 `python.exe` / `python3.exe`。

**8765 被占用**
服务会自动往后换端口，实际地址打印在窗口和控制台里。若已有本服务的实例在跑，新进程会提示「已在运行」并以退出码 3 结束，不会起第二份。

**脚本报「密钥不匹配」**
把控制台显示的 `llm-xxxxxxxx` 完整复制到脚本的答题设置里，注意别漏字符。只想本机自用可以把 `auth_mode` 改成 `any`。

**能不能自动打开浏览器**
不能，这是刻意去掉的：服务只打印地址，界面自己开。

## 隐私与安全

- 全部数据留在本机：`config.json` 存你的 API Key，`cache.json` 存问答缓存，`usage.json` 存用量。仓库里只有 `config.example.json`，**不含任何真实密钥**。
- `config.json`、`cache.json`、`usage.json`、`local-scripts/`、`qbank/` 都在 `.gitignore` 里，不会被误提交。
- 服务只绑定 `127.0.0.1`，不对局域网暴露。

## 免责声明

仅供个人学习与技术研究，请遵守所在平台的使用协议与相关法律法规。使用本工具产生的一切后果由使用者自行承担。
