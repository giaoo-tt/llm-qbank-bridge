## LLM 题库桥 v1.0.1

补丁版本：新增一套老式第三方题库脚本的兼容接口，其余行为与 v1.0.0 一致。

### 下载

| 文件 | 说明 |
| --- | --- |
| `qbank-v1.0.1-win64.zip`（8.2 MB） | Windows 单文件版，解压后双击 `qbank.exe` 即用，**无需安装 Python** |

```
qbank-v1.0.1-win64/
├── qbank.exe            服务本体（8.45 MB）
├── config.example.json  配置模板
└── 使用说明.txt         新手向步骤说明
```

### 本版改动

- **新增 `POST /search`**：题库海格式兼容接口。只会发 `{key, question, options, type}` 的老脚本（内置「题库海 / 一之 / free4」那一类），可以把题库地址直接填 `http://127.0.0.1:8765`，密钥填本机访问密钥，不用改脚本代码。
- **答案自动对回选项原文**：模型给的是文字，这类脚本要的是「和选项字符串全等」，所以按「原文全等 → 去掉前缀标点 → 字母 A/B/C → 相似度 ≥ 0.72」四级匹配；多选回数组、判断题回 `对` / `错`、对不上回空让脚本继续问别的题库。
- **脚本适配补丁增强**：现在也能识别 `http://api.tikuhai.com` 这一套脚本，把它指到本机。

### 校验

```
qbank.exe  SHA256  2d10ec9a90f1f0f3aac0e17b88c72ff1acdb8d8dd1c2459fc56e82f4a688bada
zip        SHA256  157e1e7cd4ef2ee2ef6b2cbd2ab8fc874f6eb3dac4540e8c3eb10766dd0d69c0
```

校验方法（PowerShell）：`Get-FileHash .\qbank-v1.0.1-win64.zip -Algorithm SHA256`

### 升级方式

- 用 exe 的：关掉正在运行的黑窗口，用新的 `qbank.exe` 覆盖旧的，`config.json` / `cache.json` / `usage.json` 不用动。
- 用源码的：`git pull` 后重启 `python qbank_bridge.py` 即可。

### 环境要求

- Windows 10 / 11（x64）。其他系统请用源码方式运行，Python 3.8+ 即可，无需任何第三方依赖。

### 已知限制

- exe **未做数字签名**，首次运行 Windows SmartScreen 可能提示「已保护你的电脑」，点「更多信息 → 仍要运行」。
- 只监听 `127.0.0.1`，刻意不对外网/局域网开放。
- 需要自备模型 API Key，答案质量取决于所配模型。
- 本仓库**不包含**任何网课脚本本体。
- 默认端口 8765，被占用时自动顺延，实际地址以窗口输出为准。

### 从源码运行

```bash
git clone https://github.com/giaoo-tt/llm-qbank-bridge.git
cd llm-qbank-bridge
python qbank_bridge.py
```

### 免责声明

仅供个人学习与技术研究，请遵守所在平台的使用协议与相关法律法规。使用本工具产生的一切后果由使用者自行承担。
