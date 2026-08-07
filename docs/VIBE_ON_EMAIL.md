# 发邮件时附带 Vibe 二次分析

> Vibe = [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading)，HKU 开源的个人交易 Agent。

## 条件

1. 本机 `vibe-trading serve --port 8899` 在跑  
2. `config/notify.yaml`：

```yaml
vibe:
  enabled: true
  on_email: true
  base_url: "http://127.0.0.1:8899"
  max_wait_sec: 120
```

3. 调度器正常发信（`run_scheduler --test` 或定时任务）

## 行为

- 组装与看板相同的投喂 JSON → 调 Vibe → 用 `clean_summary` 写入邮件「Vibe 二次分析」段  
- **失败/超时：邮件照发**，仅缺少 Vibe 段  
- 建议 `max_wait_sec` 不要超过 180，避免拖死整轮调度  

## 注意

- mac 定时任务若只起 GP 助手、不起 Vibe，请保持 `enabled: false` 或另写 launchd 保活 Vibe  
- Vibe 很慢时，邮件会延迟到分析结束或超时  
