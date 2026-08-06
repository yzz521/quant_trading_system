# AI 点评

```bash
tar -xzf quant_trading_system_phase25_ai_summary.tar.gz
```

config/notify.yaml:

```yaml
ai:
  enabled: true
  api_key: "sk-..."
  base_url: "https://api.deepseek.com"
  model: "deepseek-chat"
```

```bash
python examples/test_ai_summary.py
python examples/run_scheduler.py --test --market CN
```
