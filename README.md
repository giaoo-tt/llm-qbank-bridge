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
- **可打包成单文件 exe**：PyInstaller 一条命令打成约 8.4 MB 的 exe，脱离 Python 运行（见「打包」）。

## 工作原理

```
网课脚本 ──POST /api/open/v1/search/exact──▶ 本服务
                                              ├─ 缓存命中 ──▶ 直接返回（不花 token）
                                              └─ 未命中 ──▶ OpenAI 兼容 API
                                                             └─ 解析 JSON ──▶ 写缓存 ──▶ 返回同形响应
```

1. 脚本把题干、选项、题型发到本机 `127.0.0.1:8765`，请求头带 `X-Consumer-Token`。
2. 服务按「题干 + 选项 + 题型」算指纹查缓存，同一道题只真正问模型一次。
3. 未命中时拼内置提示词调用模型，要求只输出 `{"answer": [...], "confidence": x}` 这样的 JSON。
4. 解析结果按题库接口的格式包装后返回，同时累计 token 用量并折算费用。

## 系统要求

- Windows / macOS / Linux 都可以，Windows 下额外提供 `start.bat`。
- Python 3.8+，只用标准库，**不需要 `pip install` 任何东西**。
- 需要能访问你所配模型服务的网络；服务本身只监听 `127.0.0.1`，不对局域网开放。

## 目录结构

```
llm-qbank-bridge/
├── qbank_bridge.py       服务本体（单文件，只依赖标准库）
├── ui.html               控制台页面
├── start.bat             Windows 启动器（自动找可用 Python，前台运行）
├── config.example.json   配置模板（复制为 config.json 后填写）
├── docs/                 发布说明等文档
└── qbank/                打包输出目录（git 忽略，运行时生成）
```

运行后会自动在程序目录生成：`config.json`（配置）、`cache.json`（答案缓存）、`usage.json`（用量）、`local-scripts/`（适配后的脚本）。

## 快速开始

### 方式一：直接跑源码（需要 Python 3.8+）

```bash
python qbank_bridge.py
```

Windows 下也可以双击 `start.bat`：它会自动逐个探测可用的 Python（会跳过微软商店那个假的 `python.exe`），前台运行并把日志留在窗口里，关掉窗口即停止服务。

### 方式二：单文件 exe（推荐给不想装 Python 的人）

到 [Releases](https://github.com/giaoo-tt/llm-qbank-bridge/releases) 下载 `qbank-v1.0.1-win64.zip`（约 8.2 MB），解压后双击 `qbank.exe` 即可，**无需安装 Python**。

- zip 内含 `qbank.exe`、`config.example.json` 和一份 `使用说明.txt`。
- 首次运行会在 exe 同级目录生成 `config.json`，**不要放在 `C:\Program Files` 这类只读目录**，否则配置和缓存写不进去。
- exe 未做数字签名，首次运行 SmartScreen 可能提示「已保护你的电脑」，点「更多信息 → 仍要运行」。

发布说明见 [`docs/release-v1.0.1.md`](docs/release-v1.0.1.md)；想自己构建见下方「[打包](#打包)」。

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
| POST | `/search` | 题库海格式兼容接口，给只会说 `{key, question}` 的老脚本用 |

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

### 题库海格式兼容（`POST /search`）

有些老脚本（例如内置「题库海 / 一之 / free4」那类）不会说上面这套协议，而是发：

```http
POST /search
Content-Type: application/json

{ "key": "llm-xxxxxxxx", "question": "题干文字", "options": ["A、xxx", "B、yyy"], "type": "0" }
```

服务同样能接，回它认识的形状：

```json
{ "code": 1, "msg": "ok", "data": { "answer": ["A、xxx"], "num": "", "usenum": "" } }
```

- `answer` 是**选项原文数组**（单选一个、多选多个），判断题回字符串 `对` / `错`，因为这类脚本是按选项文字做全等匹配的。
- 模型返回字母（`A`）、带前缀（`A、`）、少个标点、轻微不一致时，服务会自动对回原始选项；对不上就回空，让脚本继续问别的题库。
- 失败时回 `{"code": -1, "msg": "原因"}`，密钥不对的原因会原样告诉脚本。
- 这类脚本的「题库地址」填 `http://127.0.0.1:8765`，「密钥」填本地访问密钥 `llm-xxxxxxxx` 即可，其余第三方题库（如 `http://cx.icodef.com/...`）失效也不影响本地这条链路。

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

**答案不准怎么办**
模型是按题干和选项现场推断的，没有真正的题库兜底。可以换更强的模型、把 `temperature` 保持为 `0`、或在 `system_prompt` 里补充学科背景；单选和判断题的命中率通常明显高于多选。

**会不会把我的题目传到第三方**
不会。题目只发给你在 `base_url` 里配置的那个模型服务，除此之外没有上报、遥测，也不请求任何第三方题库。

## 隐私与安全

- 全部数据留在本机：`config.json` 存你的 API Key，`cache.json` 存问答缓存，`usage.json` 存用量。仓库里只有 `config.example.json`，**不含任何真实密钥**。
- `config.json`、`cache.json`、`usage.json`、`local-scripts/`、`qbank/` 都在 `.gitignore` 里，不会被误提交。
- 服务只绑定 `127.0.0.1`，不对局域网暴露。
- **别把 `config.json` 发到群聊或贴图里**——里面的 `api_key` 就是可直接消费的凭证。怀疑泄露就去模型后台作废重发，再改 `config.json` 或控制台即可。
- 建议给 Key 设个额度上限，`quota` 字段也能在本地做费用熔断（`0` 表示只统计不限制）。

## 更新日志

### v1.0.1

- 新增 `POST /search` 题库海格式兼容接口：只会说 `{key, question}` 的老式第三方题库脚本可以直接把题库地址填成本机地址。
- 答案会按「原文全等 → 去前缀标点 → 字母 → 相似度 0.72」四级对回选项原文，多选回数组、判断题回 `对`/`错`。
- 脚本适配补丁新增识别 `http://api.tikuhai.com` 这一套脚本。

### v1.0.0

- 搜题转发、本地缓存、用量统计、网页控制台、脚本一键适配、单文件打包。
- 启动器逐个试跑候选 Python，跳过微软商店那个假 `python.exe`，避免 `9009`。
- 刻意不做「自动打开浏览器」，只打印服务地址。
- 重复启动会被识别（退出码 3），不会起第二份服务。

## 许可证

[MIT](LICENSE) © 2026 giaoo-tt

## 免责声明

仅供个人学习与技术研究，请遵守所在平台的使用协议与相关法律法规。使用本工具产生的一切后果由使用者自行承担。
