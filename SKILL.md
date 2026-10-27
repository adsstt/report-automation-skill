---
name: report-automation
description: 把人工做的日报/时报/周报变成可自动复用的报表流程。有目标模板时按模板反推并复刻；没有目标模板时，根据源数据和业务目标，在用户显式选择时间周期并确认数字口径后，自动生成管理摘要、明细数据、趋势环比三张 Sheet 的第一版标准报告。规则沉淀为 profile.yaml；之后每次只给源文件即可出 CSV 与 Excel。凡是涉及投放报表、客户报表、换产品重建报表、口径对齐或影子对账都应使用。
---

# 报表自动化（report-automation）

把「人工做报表」变成「AI 做报表」，而且能复制给别人用。

核心：**不写死某一份报表，而是把规则变成一份可读可改的配方单（profile.yaml）**。
第一次教一遍，之后照着配方单调数、出报表。

## 三条硬规则（违反任何一条都算出错）

1. **引擎里不许出现按平台分支的逻辑。** 平台差异只允许出现在两处：`references/platforms/*.md`（字段知识）与 `profile.yaml`（最终选择）。写成 `if platform == "tiktok"` 的那天，这套东西就维护不动了。
2. **数字口径不许猜。** 同一业务事件在源里存在多个候选字段时（去重实体不同 / 增量与累计并存 / 窗口不同 / 有 `_v1` 之类版本后缀），必须让用户确认。禁止按字段名后缀或"看起来最像"自动选。
3. **校验失败不许出正式报表。** 默认阻断；只有用户明确要求时才生成 `_drafts/` 下的诊断草稿，且不得覆盖正式产物。

## 先判断属于哪种场景

| 用户说 | 场景 | 做什么 |
|---|---|---|
| "帮我做今天的日报" / "出 18 点时报" | 日常出报表 | 用现有 profile 走「日常出报表」，不要重问口径 |
| "第一次用" / "把这个报表自动化" / "换产品了" | 首次接入 | 走「首次接入五步」 |
| "客户把表头改了" / "加一列" | 改格式 | 改 profile 的 `output`，跑 `verify_format.py`，不改脚本 |
| "这个指标口径变了" / "新导出多了个字段" | 改口径 | 改 `metrics`（必要时用 `effective` 分段），重跑校验与对账 |
| "你有现成的例子吗" | 参考示例 | 指向 `examples/`，并说明三道闸 |

## 首次接入先判断有没有目标模板

- **有目标模板**：继续走下面的「首次接入五步」，Probe 同时读取源数据和目标模板。
- **没有目标模板**：先让用户说明业务目标，再探源并给出日报、周报、月报或自定义周期建议。建议必须带日期覆盖证据，但不能替用户选择。

无模板模式下，用户未显式选择时间周期时必须阻断：不生成模板、不生成 DRAFT、不生成正式报告。用户选择周期后，再确认 P0 数字口径；仍有 P0 时只允许生成带黄色提示条的 `_DRAFT.xlsx`。正式报告固定包含：`管理摘要`、`明细数据`、`趋势环比`。口径与数据质量在对话中确认，不增加第四张 Sheet。

```bash
# 1) 只探源数据
python scripts/probe_sources.py --source-dir "<源目录>" --out runtime/probe.json

# 2) 写入业务目标；period.confirmed 未确认时保持 false
# runtime/business-goal.yaml

# 3) 生成标准报告候选 profile（会给周期建议）
python scripts/infer_mappings.py --probe runtime/probe.json \
  --business-goal runtime/business-goal.yaml --standard-output \
  --out runtime/profile.draft.yaml

# 4) 用户选择周期、确认 P0 后，可先生成版式预览
python scripts/create_standard_template.py --profile runtime/profile.draft.yaml \
  --out runtime/standard-report-template.xlsx --draft

# 5) 用户确认版式并把 layout_confirmed 设为 true 后生成正式模板
python scripts/create_standard_template.py --profile runtime/profile.confirmed.yaml \
  --out runtime/standard-report-template.xlsx
```

## 首次接入五步

每步都用脚本，不要凭感觉跳步。

### 1. Probe（探）

```bash
python scripts/probe_sources.py --source-dir "<源目录>" --target "<目标报表.xlsx>" --out runtime/probe.json
```

探针只"看"不下判断：编码、分隔符、表头行、列、行数、日期覆盖、总计行、粒度候选、sheet 角色、公式与条件格式。

**重点看 `ambiguity_flags`** —— 同一语义对应多个列名的地方，几乎就是后面要问用户的问题清单雏形。

### 2. Infer（反推，只产候选）

```bash
python scripts/infer_mappings.py --probe runtime/probe.json --out runtime/profile.draft.yaml
```

产物全是 `status: candidate`，带证据。边界：

- 总量相等**不足以**定论（两个字段完全可能总量相同）
- 唯一匹配 + ≥2 个日期 × ≥2 个国家样本，才算高置信
- 两个以上候选无法区分 → 转 P0 让人裁决
- **绝不自动冻结**

### 3. Draft + Diff（影子报表与逐格对账）

用候选规则把用户样例那天重跑一遍，再逐格对账：

```bash
python scripts/generate_report.py --profile runtime/profile.draft.yaml --report-type daily --date <样例日期> \
    --source-file <id>=<路径> --out-dir runtime/shadow
python scripts/shadow_diff.py --shadow runtime/shadow/过程表_<日期>.csv --expected <用户样例.csv> \
    --keys 记录键 --out runtime/shadow_diff.md
```

对不上的格子比空想更能暴露真实规则——**差异就是还没写下来的规则**。

### 4. 问人（疑点协议）

把差异与 `ambiguity_flags` 整理成疑点清单。每条固定写五项：
**问题 · 我看到的证据 · 建议值 · 影响哪些格子 · 确认后写回哪条规则**。

分级：

| 级别 | 判据 | 处理 |
|---|---|---|
| P0 阻断 | 不解决算不出正确数字（归因归属、候选字段、日期时区、账户排除、跨层混加） | 必问；未解决时 `generate_report.py` 拒绝运行 |
| P1 数值 | 能出稿但个别格子对不上（低样本保留、多国 Campaign 拆分） | 出稿时附差异清单 |
| P2 展示 | 只影响样式/标题/精度 | 按建议值默认，标注可改 |

**五类永远不许默认猜**：归因口径归属、同一事件多候选字段、账户排除名单、日期与时区口径、金额与百分比精度。

**必须联网核验的情形**（检索官方文档，把 URL 与原文摘录写进证据）：
目标格式需要但本地 catalog 没有的字段；用户给的字段名与官方不一致；源文件出现标 `unverified` 的字段；平台口径可能已变更。

### 5. Freeze（定稿）

满足全部条件才允许冻结：无未解决 P0 ＋ 影子 diff 全绿 ＋ 用户显式确认。
定稿必须写入 `verification` 证据（探了哪些文件、文件指纹、对了多少格、差几格），不是打一个"已验证"标签。

## 日常出报表

```bash
# 先校验（不通过就停）
python scripts/validate_profile.py <profile.yaml>

# 客户改过表头时先核对列能否对上
python scripts/verify_format.py --profile <profile.yaml> --target <客户工作簿.xlsx>

# CSV（审计用）
python scripts/generate_report.py --profile <profile.yaml> --report-type daily --date 2026-10-25 \
    --source-file tt_ad=<tiktok.csv> --source-file af_ad=<af.csv> --out-dir runtime/out

# 客户 Excel 工作簿（追加历史 + 环比块）
python scripts/render_workbook.py --profile <profile.yaml> --report-type daily --date 2026-10-25 \
    --source-file tt_ad=<tiktok.csv> --source-file af_ad=<af.csv> \
    --compare-date 2026-10-24 \
    --compare-source-file tt_ad=<前一日.csv> --compare-source-file af_ad=<前一日.csv> \
    --out runtime/out/客户工作簿_20261025.xlsx

# 出稿后自检
python scripts/verify_output.py   --profile <profile.yaml> ...
python scripts/verify_workbook.py --template <模板.xlsx> --output <产物.xlsx> --report-date 2026-10-25
```

`render_workbook.py` 只写**副本**，永不修改模板原件；同一天重跑会原位替换，不产生重复行。

## 用户的话怎么落成配置

| 用户表达 | 落到 profile 的哪里 |
|---|---|
| "注册要用 AppsFlyer 的，不用 TikTok 的" | `metrics.<名>.from` |
| "FTD 用带 _v1 的那个" | `metrics.<名>.candidates[].status` + `chosen` |
| "从 10-25 开始口径变了" | `metrics.<名>.effective`（`from` / `before` 分段，必须有兜底段） |
| "花费按消耗求和" | `metrics.<名>.from` + `agg: sum` |
| "按账户×国家×事件汇总" | `report_types[].aggregation.group_by` |
| "11 点时报跟前一天 11 点比" | `comparison.mode: same_point_prev_day` |
| "周报是上周 vs 本周" | `time_scope.multi_period` + `comparison.previous_period` |
| "客户把表头改名了" | 该列的 `aliases`，旧写法继续兼容 |
| "这周开始不要收入列" | `format_history` 记一笔 + 调整 `columns` |
| "备注列留空" | `constant_columns` |
| "同一个 Campaign 跑多个国家" | `rules.country_lookup` + `special_combos` |

口径与规则**只改配置，不改脚本**。改完必须跑 `validate_profile.py`。

## 门禁与草稿通道

`generate_report.py` / `render_workbook.py` 在下列情况**拒绝运行**：

- profile 校验不通过
- 存在未解决 P0（如 `chosen` 为空）
- 跨层混加、关联覆盖率不足、快照指标未成熟
- 数值对账不平、历史行被破坏

想"先看看错在哪"时：只有用户明确要求才生成诊断草稿——落在 `_drafts/`、文件名带 `_DRAFT`、表内首行写「诊断草稿-非正式交付」。

## 示例的三道闸

`examples/` 只是样板（已脱敏）。

1. **只读**：`generate_report.py` / `render_workbook.py` 拒绝用 `examples/` 下的 profile 出报表
2. **适配度检查**：`verify_profile_fit.py` 按「平台组合 × 粒度 × 目标格式结构 × 字段覆盖率」打分，不达标拒绝复用
3. **来源标注**：`origin.type = from_example` 必须重跑 probe + 影子对账才能冻结；未验证项要在交付说明里写「本配置源自示例，有 N 项未经样本验证」

## 文件地图

| 文件 | 用途 |
|---|---|
`references/report-profile-schema.json` | 配方单机器可校验格式（改配置前先看它） |
`references/report-profile-schema.md` | 配方单人读说明 |
`references/ambiguity-protocol.md` | 疑点分级与提问模板 |
`references/platforms/*.md` | 各平台字段字典与同事件候选组 |
`references/platforms/catalog.yaml` | 平台识别模式、角色与 source 前缀（引擎不写平台分支） |
`references/lookups/iso3166-1.zh.json` | 国家码基础层 |
`references/lookups/business-aggregates.yaml` | 区域聚合码、组合特例、习惯译名 |
`scripts/probe_sources.py` | 探源与目标格式 |
`scripts/infer_mappings.py` | 反推候选（只产候选） |
`scripts/create_standard_template.py` | 无目标模板时创建标准三 Sheet 模板或 DRAFT |
`scripts/validate_profile.py` | 结构 + 语义 + P0 门禁 |
`scripts/verify_format.py` | 列能否对上目标表头（可反向起草列定义） |
`scripts/verify_profile_fit.py` | 示例复用适配度打分 |
`scripts/promote_example.py` | 将只读示例复制为待 probe / 对账的项目草稿 |
`scripts/generate_report.py` | 出过程表/结果表 CSV |
`scripts/render_workbook.py` | 写客户 Excel 工作簿 |
`scripts/shadow_diff.py` | 影子报表 vs 样例逐格对账 |
`scripts/verify_output.py` | 数值层对账 |
`scripts/verify_workbook.py` | 工作簿层断言 |
`scripts/country_resolver.py` | 三层国家码解析 |
`examples/minimal/profile.yaml` | 最小及格线 |
`evals/fresh-product/run_acceptance.py` | 全新 NovaFit 产品的 Probe→Infer→生成→验证端到端验收 |

## 交付说明必须包含

- 输出文件路径、报表类型、日期
- 核心指标合计（花费/注册/KYC/FTD/FTT/收入）
- 校验结果：跑了哪些检查、是否全过；关联覆盖率与未匹配键数量
- 未解决项：还有几条 P1 建议项；若配置源自示例，写明未验证项数量
- 工作簿类交付额外说明：新增了哪些行/环比段，模板是否未被改动

## 常见错误

| 错误 | 后果 | 正确做法 |
|---|---|---|
| 直接拿 `examples/` 出报表 | 平台或格式不一致 → 数字错 | 复制到自己项目并重跑对账 |
| 按字段名后缀自动选 `_v1` | 口径错，可能几十倍偏差 | 列 P0 问用户 |
| 把 campaign/adgroup/ad 三层相加 | 花费翻倍 | 只用一层，向上汇总 |
| 从别的 profile 复制关联键 | 关联错乱、覆盖率暴跌 | 探源确认（关联键是"平台配对"属性） |
| 校验失败仍出正式报表 | 客户拿到错数 | 拒绝出稿，或走 `_drafts/` |
| 把目标表头当常量 | 客户一改版就要改代码 | 用 `aliases` + `format_history` + `by_header` 匹配 |
| 在脚本里写平台判断 | 六平台维护不动 | 差异下沉到 catalog 与 profile |

## 依赖

Python 3.10+、`openpyxl`、`pyyaml`、`jsonschema`。缺依赖时脚本会提示安装命令。
安装与三宿主目录说明见 `README.md`。
