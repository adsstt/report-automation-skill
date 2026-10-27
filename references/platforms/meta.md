# Meta Ads 字段参考

> 状态：**骨架，未用真实数据验证**（`unverified: true`）。
> 使用前必须用真实导出跑 `probe_sources.py` 核对列名，并把结果回填到本文件。

## 基本信息

| 项 | 值 |
|---|---|
| 平台 | Meta Ads（Facebook / Instagram） |
| 官方文档 | https://developers.facebook.com/docs/marketing-api/insights |
| 常见导出粒度 | Campaign / Ad Set / Ad 三级 |
| 归因 | 默认 7 天点击 + 1 天浏览（可在账户设置调整），与 TikTok 口径不同 |

## 常见字段（待真实导出核对）

| 常见列名 | 建议 canonical | 备注 |
|---|---|---|
`Campaign name` / `Campaign ID` | campaign_name / campaign_id | |
`Ad set name` / `Ad set ID` | adgroup_name / adgroup_id | Meta 用 "Ad set" 而不是 "Ad group" |
`Ad name` / `Ad ID` | ad_name / ad_id | |
`Day` / `Reporting starts` | date | 导出参数不同列名不同 |
`Amount spent (USD)` | spend | 币种可能出现在列名里 |
`Impressions` / `Reach` / `Frequency` | impressions / reach / frequency | |
`Link clicks` / `Clicks (all)` | clicks | 两者口径不同，选哪个要问 |
`Results` / `Purchases` / `Leads` | 各业务事件 | 取决于优化目标，**不是固定列** |
`Purchase ROAS` | roas | 归因窗口影响数值 |

## 风险点（接入前必须确认）

1. **`Results` 是随优化目标变化的复合列**，不能当成固定的"注册/购买"指标。
2. **归因窗口与 TikTok 不同**，双边数据不可直接对比，更不可相加。
3. Meta 的 `Ad set` 概念对应其他平台的 adgroup；关联键命名容易搞错。
4. 币种可能在列名或单独列里，跨账户合并前必须统一币种。
5. 部分导出是 **Excel 多 sheet**（每个广告账户一个 sheet），需要逐个探测。

## 与归因平台的关联键（待确认）

未知。**不要照搬 TikTok×AppsFlyer 的 `creative_id ↔ Ad ID` 结论**。
先用真实数据跑探源，验证覆盖率后再写入 profile 的 `source_plan.joins.keys`。
