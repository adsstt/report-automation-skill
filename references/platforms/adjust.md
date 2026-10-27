# Adjust 字段参考

> 状态：**骨架，未用真实数据验证**（`unverified: true`）。
> 使用前必须用真实导出跑 `probe_sources.py` 核对列名。

## 基本信息

| 项 | 值 |
|---|---|
| 平台 | Adjust（归因平台） |
| 官方文档 | https://help.adjust.com/en/article/report-metrics |
| 常见导出粒度 | Campaign / Adgroup / Creative / Ad |
| 快照特性 | 与 AppsFlyer 类似：部分指标会随归因回补变化，需要 `snapshot_date` |

## 常见字段（待真实导出核对）

| 常见列名 | 建议 canonical | 备注 |
|---|---|---|
`Day` / `Date` | date | |
`Campaign` / `Campaign ID` | campaign_name / campaign_id | Adjust 用 `campaign` 而非 campaign ID 作主键的风险 |
`Adgroup` / `Adgroup ID` | adgroup_name / adgroup_id | |
`Ad` / `Creative` | ad_name / creative_name | 层级命名与其他平台不同 |
`Installs` | install | |
`Sessions` | sessions | Adjust 特有指标 |
`Clicks` / `Impressions` | clicks / impressions | |
`Revenue` / `Revenue (USD)` | revenue | 币种混用风险 |
`Cohort` 相关列 | 各业务事件 | Adjust 的 cohort 报表与 AppsFlyer 的 ltv 口径不同 |

## 风险点（接入前必须确认）

1. **层级命名差异**：Adjust 的 `Adgroup` / `Creative` 与 TikTok 的 adgroup / creative 不是一一对应。
2. **cohort 口径**与 AppsFlyer 的 `ltv days cumulative` 概念相近但实现不同，不能混用定义。
3. 导出可能是 **CSV 或 API JSON**，字段大小写与分隔符需要探测。
4. 部分报表按事件拆分（每个事件一个报表），需要多个源合并。
5. 时区：Adjust 报表可按账户时区或 UTC 输出，**必须记录**，否则日期对齐会错一天。

## 与投放平台的关联键（待确认）

未知。Adjust 的 `Ad ID` / `Creative ID` 与投放平台哪一列对应，
必须用真实数据测覆盖率，不要照搬 AppsFlyer 结论。
