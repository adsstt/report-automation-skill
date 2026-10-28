# 配方单（profile.yaml）说明

机器可校验的格式在 `report-profile-schema.json`；本文解释每个块的用途与常见写法。

## 五个必填块

| 块 | 作用 | 一句话 |
|---|---|---|
`profile_version` | 版本 | 固定 `1` |
`product` | 产品/客户/时区 | 报表归属 |
`report_types` | 报表定义 | 看多久 × 按什么分 × 跟谁比 |
`sources` | 源文件 | 每个导出文件一条，声明平台、粒度、表头映射 |
`source_plan` | 合并去重 | 关联键、禁止跨层相加、去重策略 |
`metrics` | 指标 | 语义角色 → 源字段（支持候选与口径分段） |
`output` | 目标格式 | 写哪个工作簿/CSV、每个 sheet 的列定义 |

## report_intent：无目标模板时的报告意图

`report_intent` 是可选块；已有目标模板的 profile 不需要迁移。无模板模式用它记录业务目标、
用户选择的周期、核心 KPI、明细维度和版式确认状态。

```yaml
report_intent:
  primary_goal: efficiency
  goal_description: 降低获客成本并保持安装规模
  period:
    type: weekly                 # daily | weekly | monthly | custom
    confirmed: true              # 必须由用户显式确认
    recommendation:
      suggested_type: weekly
      options: [daily, weekly, monthly, custom]
      evidence: [源数据覆盖 21 个不同日期]
  comparison: { mode: previous_equal_period }
  primary_kpis: [spend, impressions, clicks, install]
  dimensions: [date, account_id, campaign_id, ad_id]
  layout_confirmed: false
```

`period.confirmed: false` 是硬阻断：模板、DRAFT 和正式报告都不能生成。周期确认后，未解决 P0
或未确认版式时可以显式生成 DRAFT；正式报告还要求 `layout_confirmed: true`。`custom` 周期必须
同时提供 `start` 和 `end`，对比期自动取紧邻的上一等长日期范围。

## report_types：三个正交轴

```yaml
report_types:
  - id: daily
    time_scope:  { mode: single_period, period: day, anchor: report_date }
    aggregation: { grain: ad, group_by: [account, event, country], total_row: true }
    comparison:  { mode: none }
```

- `time_scope.mode`：`single_period` / `multi_period` / `rolling_window`
- `comparison.mode`：`none` / `same_point_prev_day` / `previous_period` / `sequential_trend`

新增报表形态 = 组合已有轴值，不改引擎。

## metrics：四种写法

**直接映射**

```yaml
spend: { role: spend, from: tt_ad.spend, agg: sum }
```

**多广告平台同口径相加**

```yaml
spend:
  role: spend
  components: [meta_ad.spend, google_ad.spend]
  agg: sum
```

`components` 只用于语义一致、允许相加的同口径指标。每个广告平台仍须分别与归因源配置
并确认 join；不能用它跨粒度相加 campaign、ad group 与 ad 数据。

**多候选（同一事件多个字段，必须人确认）**

```yaml
KYC:
  role: kyc
  candidates:
    - { id: kyc_successed,    source: af_ad, field: "...kyc_successed",    semantics: {entity: unique_user, window: ltv, basis: ltv}, status: confirmed }
    - { id: kyc_successed_v2, source: af_ad, field: "...kyc_successed_v2", semantics: {entity: unique_user, window: ltv, basis: ltv, variant: v2}, status: rejected }
  chosen: kyc_successed
  confirm_required: true
```

**口径分段（口径随时间变化）**

```yaml
FTD_LTV:
  role: first_deposit
  display_only: true
  effective:
    - { from: "2026-10-25", field: af_ad.ftd_ltv_v1, note: "导出新增 _v1 列" }
    - { before: "2026-10-25", field: af_ad.ftd_ltv_old }
```

必须有一个 `before` 兜底段，否则早期日期无字段可用 → 校验器报 `EFFECTIVE_NO_FALLBACK`。

## output：列级契约

```yaml
output:
  format_id: client-2026-10-26
  format_history:
    - { date: "2026-10-26", change: "新增 FTD LTV 列、去掉收入列" }
  artifacts:
    - kind: xlsx_workbook
      write_mode: append_to_sample        # 或 new_file_per_period
      target: { mode: append_to_sample, sample: "samples/target_sample.xlsx" }
      sheets:
        - id: history_daily
          match: daily
          target_sheet: "日报-汇总（注册）"   # 目标工作簿里的实际 sheet 名
          role: history                     # history | comparison
          row_key: { field: "日期", format: "text" }
          placement: { mode: append_bottom, same_key: replace_in_place }
          column_match: by_header            # 按列名匹配，不按位置
          group_labeling: repeat             # repeat | sparse
          group_by: ["日期", "国家"]
          filter: { event: "注册" }
          total_row: { enabled: true, label_column: "国家", label: "总计" }
          constant_columns: { "备注": "" }
          columns:
            - { header: "日期", aliases: ["Date"], source: "$report_date", format: "date:yyyy-mm-dd" }
            - { header: "国家", source: "group.country", format: "text" }
            - { header: "花费($)", aliases: ["花费（$）"], source: "metrics.spend", format: "number:2" }
            - { header: "注册成本($)", source: "derived.cpa_reg", format: "number:2" }
```

支持的 `source` 命名空间：

| 前缀 | 含义 |
|---|---|
`$report_date` / `$report_point` | 报表常量 |
`$const_*` | 常量列（配合 `constant_columns`） |
`group.*` | 分组维度（country / event / account） |
`metrics.*` | 指标 |
`derived.*` | 派生指标（用 `expr` 计算） |
`dimensions.*` | 非聚合展示字段 |

`role: comparison` 的 sheet 使用三行交错块：

```yaml
          comparison_layout:
            mode: block_interleaved
            block:
              rows: [base, current, delta]
              group_by: [country]
              delta_label: "环比"
              delta_value: raw_ratio      # 存原始比例，靠格式显示百分比
              null_symbol: "/"
```

## 常见错误

| 写法 | 问题 |
|---|---|
`chosen: null` 且 `confirm_required: true` | P0 未解决，出稿会被门禁拦住（这是预期行为） |
多个 `from` 分段但无 `before` 兜底 | 早期日期无字段，校验报错 |
`column_match: by_position` | 客户一改列顺序就错位，除非模板绝对固定 |
把国家映射写死在 `rules.country_lookup.map` | 应该优先用 `references/lookups/` 三层码表，profile 只放客户特殊写法 |
