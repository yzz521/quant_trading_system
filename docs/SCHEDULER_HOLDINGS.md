# 调度器、持仓与自检

## 持仓路径
`config/holdings.db`（与看板同一库）

## 自检
```bash
python examples/check_notify.py
python examples/check_notify.py --send-test
```

## 状态
```bash
python deploy/ctl.py status
# 会附加 results/scheduler_state.json 中的最近运行摘要
```

## 测试推送
```bash
python examples/run_scheduler.py --test --market CN
```

推送可含：持仓盈亏、卖出/加仓参考、近期公司行为（分红等，尽力拉取）、自选诊断。
