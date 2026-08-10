# weekly_report（vendored）

本目录脚本来自 Codex 技能 **finance-research-report**（每周金融投研报告生成器），
仅作个人非商用使用，保留原来源与作者信息；如需分发请自行确认原项目许可。

用法（由 `weekly_report/run.py` 统一调用，一般无需直接运行）：

```bash
python quant_trading_system/weekly_report/generate_weekly_report.py \
  --stocks 000001,600519 --skip-breadth --output results/weekly/周报_2026-08-14.pdf
```

依赖：`weasyprint`（pip）+ 系统 `pango`（brew）；`ghostscript` 可选（用于 PDF 优化）。
