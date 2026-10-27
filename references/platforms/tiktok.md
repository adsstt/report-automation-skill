# TikTok Ads 字段参考

> 状态：**表头已验证**（来自真实 TikTok 批量导出），口径理解待官方文档确认。

## 基本信息

| 项 | 值 |
|---|---|
| 平台 | TikTok Ads Manager（投放平台） |
| 官方文档 | https://ads.tiktok.com/help/article/reporting-metrics |
| 真实导出形态 | `Tiktok_Ads_..._<起始日>-<结束日>.csv`，一行一个广告（含 campaign/adgroup/ad 配置与指标） |
| 表头语言 | 中文（导出语言影响列名，**必须靠 observed_schema 映射**） |

## 真实导出里出现过的列（已验证）

| 原始列名 | 建议 canonical | 说明 |
|---|---|---|
`账户 ID` / `账户名称` | account_id / account_name | 注意 ID 列名里有空格 |
`推广系列ID` / `系列名称` | campaign_id / campaign_name | |
`广告组id` / `广告组名称` | adgroup_id / adgroup_name | **`id` 是小写**，与其它列不一致 |
`广告 ID` / `广告名称` | ad_id / ad_name | 大写 `ID` |
`创意素材 ID` / `创意素材名称` | creative_id / creative_name | 归因关联用这个 |
`按天` | date | 值是 `YYYY-MM-DD` |
`消耗` | spend | |
`展示量` | impressions | |
`点击量（目标页面）` | clicks | 注意"目标页面"限定 |
`应用安装数` | install | 不是 `安装` |
`应用推广类型` | app_type | `应用安装量` / `应用重定向` |
`推广系列预算` / `广告组预算` | campaign_budget / adgroup_budget | CBO 时广告组预算可能是 `使用推广系列预算` |
`推广系列预算类型` / `广告组预算类型` | campaign_budget_type / adgroup_budget_type | `不限` / `日预算` |
`竞价策略` | bid_strategy | `最大投放量` / `成本上限` |
`出价` / `出价（学习阶段）` | bid / learning_bid | 空或 `-` 表示不设出价 |
`出价对象` | bid_object | `注册` / `加入购物车` / `付费` / `下单` |

## 常见坑

1. **同一份导出含三层粒度**：campaign / adgroup / ad 都在行上，**不能把三层相加**。
   需要更高层汇总时，先在分析粒度聚合再向上汇总。
2. **列名大小写不一致**（`广告组id` vs `广告 ID`），任何按列名匹配都要容错。
3. **投放平台的转化数与归因平台不可相加**，也不应互相替代（谁为准要问客户）。
4. **ABO/CBO 判断**：`推广系列预算类型=不限` + `广告组预算类型=日预算` → ABO；
   反向 → CBO；两个都空 → 待确认，**不得当 0 处理**。
5. 素材类型、端口（IOS/AND）、事件分类通常从**名称**解析，不是独立列。

## 多平台接入提醒

Meta / Google 的字段名、粒度层级、归因方式都与 TikTok 不同；
**关联键与字段映射必须按平台配对重新探源**，不要复制本文件结论。
