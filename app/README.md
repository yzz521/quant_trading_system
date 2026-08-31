# GP助手 · 桌面应用（可安装）

把「Streamlit 看板 + 每日邮件调度」打包成原生桌面应用：双击图标打开独立窗口，
无需终端、无需浏览器标签页；关窗即停调度（开窗即恢复推送）。

## 快速体验（开发模式，本机已有 Python 环境）

```bash
pip install -e ".[data,dashboard,gui]"
python app/main.py
```

- 自动检测 8502 端口，被占用时自动换空闲端口
- 日志：`results/app.log`（应用）、`results/dashboard.log`（看板子进程）

## 打包安装包（PyInstaller，三端）

每个目标平台**各自**执行（在对应系统上构建）：

```bash
pip install pyinstaller
# macOS / Linux / Windows
pyinstaller app/packaging/gp_assistant.spec --noconfirm
```

产物：
| 平台 | 产物 | 备注 |
|------|------|------|
| macOS | `dist/GP助手.app` | 双击即用；可 `codesign`/`notarize` 后分发 |
| Windows | `dist/GP助手/GP助手.exe` | Win10/11 自带 Edge WebView2 运行时 |
| Linux | `dist/GP助手/GP助手` | 需 `python3-gi gir1.2-webkit2-4.1`（见下） |

## 平台依赖

| 平台 | 依赖 |
|------|------|
| macOS | 无（WKWebView 内置） |
| Windows | Edge WebView2 运行时（Win10/11 通常已内置） |
| Linux | `sudo apt install python3-gi gir1.2-webkit2-4.1` |

## 数据与配置

应用**不把数据打进安装包**，首次运行时在可执行文件旁自动创建：

```text
GP助手/
├── GP助手.app        # macOS（或 GP助手.exe / GP助手）
└── config/
    ├── notify.yaml   # ← 复制 notify.yaml.example 填写推送凭证
    ├── holdings.db   # 持仓数据库（自动创建）
    └── users.yaml    # 看板登录（可选）
```

也可用环境变量 `QTS_DATA_DIR` 指定数据目录（如 `~/Library/Application Support/GP助手`）。

## 定时推送并入应用

- **应用打开** → 调度器线程启动，按 `config/notify.yaml` 的 `schedule` 在
  对应市场交易时段自动推送每日邮件（持仓/资金/今日机会/卖出参考）
- **应用关闭** → 调度器停止（不会发送邮件）
- 想完全脱离应用跑推送（如服务器）→ 仍可用原方式：
  `python deploy/ctl.py scheduler start`

## 已知事项

- 首次打开看板需 3-10 秒（加载行情数据），窗口会先白屏后显示内容
- 全市场扫描（今日机会）首次约 30-60 秒，之后有 10 分钟缓存
- 打包体积约 300-500MB（含 akshare/yfinance/streamlit 运行时），属正常
