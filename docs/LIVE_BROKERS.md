# 实盘适配器与 SDK 接入说明

仓库里的 `execution/ctp_broker.py`、`ibkr_broker.py`、`binance_broker.py` **不是**内置的可执行交易程序，而是**接口骨架**：方法签名与 `LiveBroker` 对齐，网络调用处为 `NotImplementedError`，避免误下真单。

## SDK 从哪里来？

| 通道 | Python 包 / SDK | 获取方式 | 建议先测 |
|------|-----------------|----------|----------|
| **Binance** | `python-binance` | `pip install python-binance` | Testnet |
| **IBKR** | `ib_insync` 或 TWS 自带 `ibapi` | `pip install ib_insync`；需安装 [TWS/IB Gateway](https://www.interactivebrokers.com) | Paper 账户 7497 |
| **CTP 期货** | `vnpy_ctp` 或期货公司提供的 `thosttraderapi` | 期货账户 + SimNow 仿真；见 [SimNow](http://www.simnow.com.cn/) / vn.py 文档 | SimNow |

本仓库 **不会** 随代码分发券商密钥或 CTP 动态库；需自行安装对应 SDK，并在适配器中填写连接参数。

## 推荐接入顺序

1. **PaperBroker**（已可用）跑通信号→风控→成交→日志  
2. **Binance Testnet**（接口最简单，验证 Live 链路）  
3. **IBKR Paper** 或 **CTP SimNow**

## 接入步骤（以 Binance 为例）

1. 安装：`pip install "quant-trading-system[live]"` 或 `pip install python-binance`  
2. 环境变量：`BINANCE_API_KEY` / `BINANCE_API_SECRET`（**不要**写入 git）  
3. 编辑 `execution/binance_broker.py`：实现 `connect` / `place_order` / `cancel_order` / `get_position` / `get_cash`  
4. 用 `LiveTradingEngine(BinanceBroker(...), LiveConfig(readonly=True))` 先只读，再关掉 readonly  
5. 成交回调里向 `EventEngine` 推送 `FillEvent`，与回测路径一致  

CTP / IBKR 同理：在对应文件的 `# TODO` 处接官方 API，**仿真账户验证后再上实盘**。

## 和「持仓助手」的关系

定时邮件 / 卖出区间属于 **助手轨道**，默认**不下单**。实盘下单属于 **框架轨道**，需显式接入 Broker。二者共用事件与风控概念，但启动路径不同：

- 助手：`python deploy/ctl.py start-all --with-scheduler`  
- 实盘：`examples/live_paper_trading.py` → 换成真实 Broker  
