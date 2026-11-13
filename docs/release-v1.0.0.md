## LLM 题库桥 v1.0.0

把网课脚本的「搜题」请求，接到你自己的大模型 API 上的本地小服务。只监听 `127.0.0.1`，题目只发给你配置的模型服务，不经过任何第三方题库。

### 下载

| 文件 | 说明 |
| --- | --- |
| `qbank-v1.0.0-win64.zip`（8.2 MB） | Windows 单文件版，解压后双击 `qbank.exe` 即用，**无需安装 Python** |

zip 内包含：

```
qbank-v1.0.0-win64/
├── qbank.exe            服务本体（8.4 MB）
├── config.example.json  配置模板
└── 使用说明.txt         新手向步骤说明
```

### 这个版本包含

- **搜题转发**：`POST /api/open/v1/search/exact`，请求与响应和常见题库接口同形，脚本无需改代码即可接。
- **本地答案缓存**：同一道题（题干 + 选项 + 题型）只真正问一次模型。
- **用量与费用**：按输入/输出单价折算，分「今日 / 累计」，可一键重置。
- **网页控制台**：改配置、看实时日志、查缓存、跑自测、清缓存。
- **访问密钥**：脚本必须带 `X-Consumer-Token`，避免本机其它程序蹭用额度。
- **脚本一键适配**：`/api/patch` 把脚本里的题库地址批量替换成本机地址。
- **单文件 exe**：PyInstaller 打包，脱离 Python 运行。

### 校验

```
qbank.exe   SHA256 ca1a5f2c00d8147dbd62729de682d9313c6cddee2d5b3fd16dfc00ee91df5d58
zip         SHA256 a244e88d420dbe286e78d0d1c78876ba89e17b2e2b9267cf4e7a9ccd6ee78088
```

校验方法（PowerShell）：`Get-FileHash .\qbank-v1.0.0-win64.zip -Algorithm SHA256`

### 三步开始用

1. 解压到一个**可写**目录（例如 `D:\Tools\qbank`），别放 `C:\Program Files`。
2. 双击 `qbank.exe`，按窗口提示在浏览器打开 `http://127.0.0.1:8765`。
3. 控制台里填 `api_key`（`base_url` 默认已指向 DeepSeek），点「自测」确认能调通，再把显示的地址和访问密钥填进网课脚本。

### 环境要求

- Windows 10 / 11（x64）。其他系统请用源码方式运行，Python 3.8+ 即可，无需任何第三方依赖。

### 已知限制

- exe **未做数字签名**，首次运行 Windows SmartScreen 可能提示「已保护你的电脑」，点「更多信息 → 仍要运行」。
- 只监听 `127.0.0.1`，刻意不对外网/局域网开放。
- 需要自备模型 API Key，答案质量取决于所配模型。
- 本仓库**不包含**任何网课脚本本体，脚本请自行获取并按 `/api/patch` 的说明适配。
- 默认端口 8765，被占用时自动顺延，实际地址以窗口输出为准。

### 从源码运行

```bash
git clone https://github.com/giaoo-tt/llm-qbank-bridge.git
cd llm-qbank-bridge
python qbank_bridge.py
```

Windows 也可直接双击 `start.bat`（会逐个探测可用 Python，跳过微软商店的占位 `python.exe`）。

### 免责声明

仅供个人学习与技术研究，请遵守所在平台的使用协议与相关法律法规。使用本工具产生的一切后果由使用者自行承担。
