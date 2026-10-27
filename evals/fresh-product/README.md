# NovaFit 全新用户 / 全新产品验收

这是一个完全独立的全新产品场景：

- 产品：NovaFit（虚构健身 App）
- 投放平台：Meta Ads，源文件为带说明行的 XLSX
- 归因平台：Adjust，源文件为 CSV 快照
- 国家：美国、英国
- 日期：2026-10-24、2026-10-25
- 目标格式：日报历史 sheet + 三行交错环比 sheet

运行：

```bash
python evals/fresh-product/run_acceptance.py
```

脚本会验证：XLSX/CSV 探源、未知口径门禁、已确认 profile、字段格式、配置驱动 join、
快照去重、100% 关联覆盖、数值对账、影子 diff、Excel 历史写入、环比计算和模板保护。
