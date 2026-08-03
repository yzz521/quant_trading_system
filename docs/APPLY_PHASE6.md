# 阶段六：Paper / Live 线程安全队列 + 订单状态 / 对账

## 文件

- `core/engine.py` — `EventEngine(thread_safe=True)` 使用 `queue.Queue`
- `execution/paper_broker.py` — 订单状态、幂等 `order_id`、`reconcile()`
- `execution/live_engine.py` — 默认 `thread_safe=True`
- `tests/test_thread_safe_engine.py`

## 行为

| 模式 | 队列 | 场景 |
|------|------|------|
| `thread_safe=False`（默认） | `deque` | 回测，最快 |
| `thread_safe=True` | `queue.Queue` | Paper/Live，行情线程可并发 `put` |

回测路径**无需改配置**；`LiveEngine` 已自动开启线程安全。
