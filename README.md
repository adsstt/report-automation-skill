# report-automation

把「人工做报表」变成「AI 做报表」的一个可复制技能包。
新用户提供源数据样本和业务目标即可开始。有目标报表样本时按模板复刻；没有目标模板时，
在用户选择时间周期并确认数字口径后，自动生成第一版标准三 Sheet 报告。规则会固化成
`profile.yaml`（配方单）；之后每次只给源文件，就能自动产出日报 / 时报 / 周报。

## 它解决什么问题

- 换产品、换平台就要重写一套报表脚本 → 现在换成改配置
- 客户改表头、加减列就要改代码 → 现在改配置 + 别名
- 口径随时间变化（字段改版）就要改代码 → 现在按日期分段配置
- 报表逻辑散落在脚本里，没人说得清 → 现在全部落在可读的 profile 里

## 安装

技能主体是 Markdown + Python 脚本，仓库根目录另外包含一个轻量 DSH 安装适配层。

### DSH：从 npm 官方源安装

在 DSH 的「添加插件」中保持安装源为「npm 官方源」，输入：

```text
report-automation-skill
```

### DSH：从 Git 仓库安装

在 DSH 的「插件 → 从 Git 仓库安装」中填写：

```text
https://github.com/adsstt/report-automation-skill
```

两种安装方式都会让 DSH 通过 pnpm 安装适配层；适配层启动后把技能文件同步到
`~/.agents/skills/report-automation/`。重新打开会话后即可调用。

### 其他宿主：放到技能目录

### 1. 放到宿主的技能目录

| 宿主 | 目标目录 |
|---|---|
| DSH | `~/.agents/skills/report-automation/` |
| Codex CLI | `~/.codex/skills/report-automation/` |
| Claude Code | `~/.claude/skills/report-automation/` |

复制整个目录即可，不需要软链。DSH 用户使用上面的 Git 安装方式时不需要手动复制。

### 安装 Python 依赖

```bash
pip install openpyxl pyyaml jsonschema
```

也可以使用受版本范围约束的依赖清单：

```bash
pip install -r requirements.txt
```

要求 Python 3.10+。脚本缺依赖时会给出明确提示。

### 确认能被调用

新开一个会话说「帮我做日报，这是我的源数据和目标报表」——技能应该被触发。
没有被触发就显式说「用 report-automation 技能」。

## 第一次用（教它一遍）

### 有目标模板

```bash
# 1) 探源 + 探目标格式
python scripts/probe_sources.py --source-dir <源目录> --target <目标报表.xlsx> --out runtime/probe.json

# 2) 反推候选（只产候选，不落定）
python scripts/infer_mappings.py --probe runtime/probe.json --out runtime/profile.draft.yaml

# 3) 看还缺什么、哪些必须问人
python scripts/validate_profile.py runtime/profile.draft.yaml

# 4) 核对列是否都能对上目标表头（可从目标反推列定义草稿）
python scripts/verify_format.py --target <目标报表.xlsx> --emit-yaml
python scripts/verify_format.py --profile runtime/profile.draft.yaml --target <目标报表.xlsx>
```

把 `validate_profile.py` 报的 P0 问题逐条问清楚、写进 profile，再用样本跑一遍
影子对账（`shadow_diff.py`），差异归零后可定稿。

### 没有目标模板

先提供源数据与业务目标。AI 会根据日期覆盖和目标建议日报、周报、月报或自定义周期，
但必须由用户显式选择。未选择时所有工作簿生成都会被阻断。

```bash
# 1) 只探源
python scripts/probe_sources.py --source-dir <源目录> --out runtime/probe.json

# 2) 准备业务目标（示例）
# primary_goal: efficiency
# goal_description: 降低获客成本并保持安装规模
# period:
#   type: weekly
#   confirmed: true
# layout_confirmed: false

# 3) 生成候选 profile；如未选择 period，只输出建议并保持门禁
python scripts/infer_mappings.py --probe runtime/probe.json \
  --business-goal runtime/business-goal.yaml --standard-output \
  --out runtime/profile.draft.yaml

# 4) 周期已选但口径或版式未确认时，只能预览 DRAFT
python scripts/create_standard_template.py --profile runtime/profile.draft.yaml \
  --out runtime/standard-report-template.xlsx --draft

# 5) 确认 P0 与版式后生成正式模板
python scripts/create_standard_template.py --profile runtime/profile.confirmed.yaml \
  --out runtime/standard-report-template.xlsx

# 6) 用现有引擎填数
python scripts/render_workbook.py --profile runtime/profile.confirmed.yaml \
  --report-type standard --date 2026-10-25 --source-file <id>=<源文件> \
  --out runtime/标准报告_20261025.xlsx
```

标准报告固定包含 `管理摘要`、`明细数据`、`趋势环比`。管理摘要和趋势环比展示上期、
本期、绝对变化与变化率；多广告平台时同时展示“全部平台”合计和各平台分项。明细按
真实源日期、平台、账户及可用广告层级展开。源数据存在时会保留完整漏斗指标（安装、
注册、KYC、首充、交易、收入），并自动补充点击率、各层转化率、CPI、注册成本、首充
成本和 ROAS 等可计算指标；缺字段就不展示，不能用 0 冒充。缺少上期或分母为零时显示
`N/A`。

若从内置示例开始，先复制为项目草稿，不能直接用 `examples/` 出正式报告：

```bash
python scripts/promote_example.py --example examples/minimal/profile.yaml \
  --out <项目>/.report-automation/<产品>/profile.yaml
```

## 让它记住产品规则

首次定稿后，把 profile 注册到当前工作区。以后新会话可以用产品名或别名找到它，用户不必
反复说明同一套字段映射：

```bash
python scripts/register_product_profile.py --workspace . \
  --profile runtime/profile.confirmed.yaml --alias "产品简称"
python scripts/resolve_product_profile.py --workspace . --product "产品简称" --json
```

用户可用截图、表格、文档或自然语言补充字段关系和产品规则。宿主 AI 会先把内容整理为
`knowledge-update.yaml`，保留附件引用和证据，然后预览变更。数字口径等 P0 规则必须得到用户
明确确认才能写入；写入会自动备份旧 profile 并保留审计历史。

```bash
# 预览，不修改文件
python scripts/update_product_knowledge.py --workspace . \
  --profile .report-automation/products/<产品>/profile.yaml \
  --update runtime/knowledge-update.yaml

# 确认后写入
python scripts/update_product_knowledge.py --workspace . \
  --profile .report-automation/products/<产品>/profile.yaml \
  --update runtime/knowledge-update.yaml --apply
```

知识保存在项目的 `.report-automation/`，不在 npm 安装目录内，因此升级或重装 skill 不会清空。
公共平台字典和客户产品知识彼此隔离。CLI 不自带 OCR；截图由正在使用该 skill 的多模态 AI
读取，截图内容先作为证据/候选，不能绕过 P0 确认门禁。

## 以后每次出报表

```bash
# CSV（审计用）
python scripts/generate_report.py --profile <profile.yaml> --report-type daily --date 2026-10-25 \
    --source-file tt_ad=<tiktok.csv> --source-file af_ad=<af.csv> --out-dir runtime/out

# 客户 Excel 工作簿（追加历史 + 环比块）
python scripts/render_workbook.py --profile <profile.yaml> --report-type daily --date 2026-10-25 \
    --source-file tt_ad=<tiktok.csv> --source-file af_ad=<af.csv> \
    --compare-date 2026-10-24 --compare-source-file tt_ad=<前一日 tiktok.csv> --compare-source-file af_ad=<前一日 af.csv> \
    --out runtime/out/客户工作簿_20261025.xlsx

# 出稿后自检
python scripts/verify_output.py --profile <profile.yaml> ...
python scripts/verify_workbook.py --template <模板.xlsx> --output <产物.xlsx> --report-date 2026-10-25
```

## 改格式怎么说

| 你说 | 它改哪里 |
|---|---|
| "注册成本那列标红" | 列的 `colorize` / 条件格式 |
| "加一列 KYC 率" | profile 的 `derived` + 目标 sheet 的 `columns` |
| "客户把表头改名了" | 该列的 `aliases`，旧写法继续兼容 |
| "这周开始收入列不要了" | `format_history` 记一笔 + 去掉对应列 |
| "这个指标计算口径变了" | `metrics.<名>.effective` 按日期分段 |

## 主要脚本各管一件事

| 脚本 | 管什么 | 什么时候用 |
|---|---|---|
`probe_sources.py` | 看源文件与目标格式长什么样 | 第一次接入 |
`infer_mappings.py` | 反推候选映射（不落定） | 第一次接入 |
`create_standard_template.py` | 无目标模板时创建标准三 Sheet 模板 | 第一次接入 / 版式调整 |
`register_product_profile.py` | 将定稿配置注册到工作区产品库 | 第一次 Freeze 后 |
`resolve_product_profile.py` | 按产品名/别名复用已确认配置 | 每个新会话开始 |
`update_product_knowledge.py` | 安全写入截图、表格或对话中确认的新知识 | 用户补充规则时 |
`validate_profile.py` | 配方单结构与红线校验 | 每次改 profile 后 |
`verify_format.py` | 列能不能对上目标表头 | 客户改版后 / 接入时 |
`generate_report.py` | 出过程表/结果表 CSV | 每次 |
`render_workbook.py` | 写客户 Excel 工作簿 | 每次（需要 Excel 时） |
`shadow_diff.py` | 影子报表 vs 用户样例逐格对账 | 接入时 / 排查差异 |
`verify_output.py` | 数值层对账 | 每次 |
`verify_workbook.py` | 工作簿层断言（历史未破坏等） | 每次 |

## 三条硬规则

1. **数字口径不许猜。** 同一事件存在多个候选字段时必须问人，禁止按字段名后缀自动选。
2. **校验失败不许出正式报表。** 只能生成 `_drafts/` 里的诊断草稿。
3. **示例不能直接拿来出报表。** `examples/` 是只读样板，必须复制到自己项目并重跑对账。

## 目录说明

```
report-automation/
├── SKILL.md                   # 给 AI 看的执行手册（含完整流程与红线）
├── README.md                  # 本文件：给人和新用户看
├── references/
│   ├── platforms/             # 各广告/归因平台字段字典（按需扩充）
│   ├── lookups/               # 三层国家码表（ISO / 业务聚合 / profile 覆盖）
│   ├── report-profile-schema.json  # 配方单机器可校验格式
│   ├── knowledge-update-schema.json # 产品知识更新机器契约
│   ├── report-profile-schema.md    # 配方单人读说明
│   └── ambiguity-protocol.md       # 疑点分级与提问模板
├── examples/
│   └── minimal/               # 最小及格线示例
├── scripts/                   # 生成、渲染、校验、示例转正等脚本
├── assets/templates/
└── evals/evals.json           # 触发与效果评测用例
```

## 兼容性与边界

- 支持平台：TikTok / Meta / Google × AppsFlyer / Adjust（引擎不含任何平台分支，差异全在配置与字段字典）
- CLI 不内置 OCR；宿主多模态 AI 可读取截图并转为待确认的结构化知识更新
- 不做：飞书多维表同步、图形界面
- 客户工作簿只写副本，永不修改模板原件

## 回归测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖公开 CLI 边界，包括通用 join/dedupe、缺源门禁、XLSX 输入、平台推断、
环比计算、低覆盖率、快照成熟度、示例只读和公式注入防护。

完整的新用户/新产品验收使用独立的 NovaFit（Meta Ads × Adjust）模拟数据：

```bash
python evals/fresh-product/run_acceptance.py
```

验收产物会写入 `evals/fresh-product/runtime/`，包括 probe、infer 草稿、CSV、影子 diff、
客户 Excel 和最终 `acceptance_result.md`。

另有三套“打包 → 隔离安装 → DSH 同步 → 交互门禁 → 出报表”的新用户实用性矩阵，覆盖：

- 有模板、熟悉字段：TikTok + AppsFlyer，并验证注册产品、补充知识、下一会话按别名复用
- 无模板、完全小白：Meta + Google + Adjust（标准三 Sheet 同时展示合计、平台分项和注册→KYC→首充漏斗）
- 有模板、不懂归因口径：Google + AppsFlyer（Activity/LTV 候选必须先确认）

```bash
python evals/usability-matrix/run_matrix.py
```

结果与工作簿写入 `evals/usability-matrix/runtime/`。该目录不会发布；矩阵脚本和说明会随 npm 包提供，
方便安装者在自己的环境复验。
