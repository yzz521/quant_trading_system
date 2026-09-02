GP助手 · 桌面应用（解压即用）
============================

【Windows】
  1. 解压整个 GPAssistant 文件夹，不要只拷贝其中一个 exe。
  2. 双击 GPAssistant.exe。
  3. 若弹出「Windows 已保护你的电脑」（SmartScreen）：
       点击「更多信息」→「仍要运行」。
     这是未购买代码签名证书的正常提示，不是病毒。
  4. 若提示缺少 WebView2：安装
       https://developer.microsoft.com/microsoft-edge/webview2/
  5. 杀毒软件可能拦截首次启动，请加入白名单后重试。
  6. 日志：GPAssistant 文件夹旁的 results\app.log 与 dashboard.log

【macOS · 没有 Apple 开发者账号 / 没有公证也可以用】
  从网上下载的未签名 app，系统会提示「无法验证开发者」或「已损坏」。
  任选一种方式即可（都不需要付费账号）：

  方式 A（推荐）：双击「首次打开.command」
    脚本会去掉隔离属性并做本机临时签名，然后打开 GP助手.app。
    若 .command 本身被拦截：按住 Control 点它 → 打开。

  方式 B：终端执行（把路径换成你的解压目录）
    xattr -cr ~/Downloads/GP助手.app
    codesign --force --deep --sign - ~/Downloads/GP助手.app
    open ~/Downloads/GP助手.app

  方式 C：系统设置
    打开失败后到「系统设置 → 隐私与安全性」点「仍要打开」。

  请下载与芯片匹配的包：Apple Silicon 用 arm64，Intel 用 x64。

【便携版（start.bat / start.command）】
  若下载的是 quant_trading_system-portable-* ，请双击 start.bat（Windows）
  或 start.command（macOS）。macOS 同样可用上面的 xattr 命令解除拦截。
