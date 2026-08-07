# 阶段十三：多源降级 + Paper 定时拉行情

```python
from quant_trading_system.data import FallbackDataSource, AkShareSource, LocalParquetSource
src = FallbackDataSource([AkShareSource(), LocalParquetSource("data_cache")])

from quant_trading_system.execution import LiveBarPoller
poller = LiveBarPoller(engine, src, ["600000"], interval_sec=60)
poller.start()
```

```bash
python examples/paper_poll_demo.py --seconds 5
```
