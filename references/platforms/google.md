# Google Ads 字段参考

> 状态：**骨架，未用真实数据验证**（`unverified: true`）。
> 使用前必须用真实导出跑 `probe_sources.py` 核对列名。

## 基本信息

| 项 | 值 |
|---|---|
| 平台 | Google Ads |
| 官方文档 | https://support.google.com/google-ads/answer/2548851 |
| 常见导出粒度 | Campaign / Ad group / Ad / Keyword |
| 归因 | Google 默认按点击时间归因，模型（last click / data-driven）可配置 |

## 常见字段（待真实导出核对）

| 常见列名 | 建议 canonical | 备注 |
|---|---|---|
`Campaign` / `Campaign ID` | campaign_name / campaign_id | |
`Ad group` / `Ad group ID` | adgroup_name / adgroup_id | |
`Ad name` / `Ad ID` | ad_name / ad_id | |
`Day` / `Date` | date | 格式可能是 `YYYY-MM-DD` 也可能是 `M/D/YYYY` |
`Cost` / `Spend` | spend | 或 `Cost (converted)` |
`Impressions` / `Clicks` / `CTR` | impressions / clicks / ctr | |
`Conversions` / `All conv.` | conversions | Google 的 conversions 是加权计数的，**不是去重人数** |
`Conv. value` | revenue | 与收入口径不同 |
`Install (Firebase)` / `First open` | install | 应用推广专用列 |

## 风险点（接入前必须确认）

1. **`Conversions` 是加权计数**（可含 fractional），与"去重用户数"完全不同，
   不能与 AppsFlyer/Adjust 的 unique users 相加减。
2. **归因模型**（last click / data-driven）直接影响 Conversins 归属，必须记录。
3. 导出支持 **CSV / Excel / Google Sheets**，列名随界面语言变化。
4. 关键词层级（Keyword）是 Google 独有粒度，其他平台没有，profile 的 `grain` 枚举需要扩展才能支持。
5. PMax（Performance Max）的资产组层级与常规 campaign 不同。

## 与归因平台的关联键（待确认）

未知。Google 侧 `Ad ID` 与归因平台的 `Ad ID` 是否等价需实测覆盖率，不要默认相等。
