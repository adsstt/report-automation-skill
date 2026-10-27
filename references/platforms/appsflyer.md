# AppsFlyer 字段参考

> 状态：**部分已验证**（TikTok×AppsFlyer 链路有真实数据验证）。
> `unverified: true` 的条目不得作为权威依据，需查官方文档或问 AM。

## 基本信息

| 项 | 值 |
|---|---|
| 平台 | AppsFlyer（归因平台） |
| 官方文档 | https://support.appsflyer.com/hc/en-us/articles/208387843-Raw-data-field-dictionary |
| 常见导出粒度 | Campaign ID / Adset ID / Ad ID 三份 |
| 是否需要快照口径 | 是（`snapshot_date` + `report_date`，累计指标会随重导回补） |

## 字段命名规律

`<Entity> <window> days cumulative appsflyer <event>[_v1]`

- **Entity**：`Unique users`（去重用户）/ `Events`（事件次数）/ `Revenue`
- **window**：`ltv`（按安装 cohort 累计）/ `activity`（按事件发生日累计）
- **event**：`signup_success` / `kyc_successed` / `first_deposited` / `first_traded_v1` / `trade_all` / …
- **`_v1` 后缀**：版本化指标。**新版本发布后旧字段与 `_v1` 会分叉**

## 常用字段（真实导出里确认存在）

| 官方字段 | 对应角色 | 常见坑 |
|---|---|---|
`Installs appsflyer` | install | 与投放平台的安装数不是同一口径，不能混用 |
`Unique users ltv days cumulative appsflyer signup_success` | registration | 与投放平台"去重注册次数"数值不同（归因归属问题） |
`Unique users ltv days cumulative appsflyer kyc_successed` | kyc | 与 `…kyc_successed_v2` **并存**，选哪个必须问人 |
`Unique users activity days cumulative appsflyer first_deposited_v1` | first_deposit（业务口径） | 缺失即阻断；不得回退到无 `_v1` 字段 |
`Unique users ltv days cumulative appsflyer first_deposited` | first_deposit（LTV 展示口径） | 只展示，不参与成本 |
`Unique users activity days cumulative appsflyer first_traded_v1` | trade | 与 `trade_all`、`…ltv…first_traded_v1` 是不同口径 |
`Unique users ltv days cumulative appsflyer trade_all` | trade（历史口径） | 2026-10 前旧报表用过；与新口径不可相加 |
`Revenue activity days appsflyer` | revenue | 与 event 口径需对齐 |
`Retention rate 1/7 days on-period appsflyer` | retention | 未成熟窗口不得当 0% |

## 同一事件的候选组（本平台最容易出错的地方）

### 首次入金（first_deposit）

| 候选 | 语义 | 用途 |
|---|---|---|
`…activity…first_deposited_v1` | 事件日归属 + 版本 v1 | **当前业务口径**（成本、转化） |
`…activity…first_deposited` | 事件日归属，无版本 | 已废弃，不得替代 v1 |
`…ltv…first_deposited` | 安装 cohort 归属 | LTV 展示 |

### KYC

`…kyc_successed` 与 `…kyc_successed_v2` 并存。**选哪个必须问用户**，不要按 `_v2` 更新就自动选。

### 交易（trade）

`…first_traded_v1`（activity / ltv）、`…trade_all`（ltv / average count）四种组合，
数值差异可达数倍，方向也不稳定（实测 activity 与 ltv 谁高谁低会变）。

## 已验证的实测结论（2026-10-26，TikTok×AF 链路）

1. **`_v1` 是版本化字段**：2026-10-23/24 的导出里 `activity…first_deposited_v1` 与无后缀**逐行相等**；
   2026-10-25 起出现分叉（2 行 `v1=1` 而旧字段 `=0`）→ **不能用旧字段代替 `_v1`**。
2. **`ltv` 列上线更晚**：2026-10-25 起导出才出现 `…ltv…first_deposited_v1`。
   → 同一天不同日期口径不同，必须用 `metrics.effective` 分段，不能写死常量。
3. **`activity` 与 `ltv` 是两个归属口径**，ltv 通常 ≤ activity，二者**不可相加、不可互换**。
4. **Ad ID ↔ 投放平台创意素材 ID** 才是正确关联键（不是投放平台的广告 ID）。
   ⚠️ 这是 TikTok×AF 这一对平台的结论，其他平台组必须重新确认。

## 待验证（unverified）

- `_v1` 的确切定义（修了去重？还是新增归因窗口？）——官方 Activity metrics 页 403，raw data 字典未逐字解释
- ltv 口径是否随重导历史持续回补——需要同一日期多次导出对比
- `kyc_successed` 与 `_v2` 的业务差异
