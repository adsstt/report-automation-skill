# report-automation

把「人工做报表」变成「AI 做报表」的一个可复制技能包。
新用户只需**一次**提供源数据样本 + 目标报表样本，本技能会把报表规则理清、固化成
`profile.yaml`（配方单）；之后每次只给源文件，就能自动产出日报 / 时报 / 周报。

## 它解决什么问题

- 换产品、换平台就要重写一套报表脚本 → 现在换成改配置
- 客户改表头、加减列就要改代码 → 现在改配置 + 别名
- 口径随时间变化（字段改版）就要改代码 → 现在按日期分段配置
- 报表逻辑散落在脚本里，没人说得清 → 现在全部落在可读的 profile 里

## 三步安装

本技能是纯 Markdown + Python 脚本，三个宿主通用。

### 1. 放到宿主的技能目录

| 宿主 | 目标目录 |
|---|---|
| DSH | `~/.agents/skills/report-automation/` |
| Codex CLI | `~/.codex/skills/report-automation/` |
| Claude Code | `~/.claude/skills/report-automation/` |

复制整个目录即可，不需要软链。

### 2. 装依赖

```bash
pip install openpyxl pyyaml jsonschema
```

也可以使用受版本范围约束的依赖清单：

```bash
pip install -r requirements.txt
```

要求 Python 3.10+。脚本缺依赖时会给出明确提示。

### 3. 确认能被调用

新开一个会话说「帮我做日报，这是我的源数据和目标报表」——技能应该被触发。
没有被触发就显式说「用 report-automation 技能」。

## 第一次用（教它一遍）

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

若从内置示例开始，先复制为项目草稿，不能直接用 `examples/` 出正式报告：

```bash
python scripts/promote_example.py --example examples/minimal/profile.yaml \
  --out <项目>/.report-automation/<产品>/profile.yaml
```

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

## 三个脚本各管一件事

| 脚本 | 管什么 | 什么时候用 |
|---|---|---|
`probe_sources.py` | 看源文件与目标格式长什么样 | 第一次接入 |
`infer_mappings.py` | 反推候选映射（不落定） | 第一次接入 |
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
- 不做：截图 OCR 数据源（接口预留，调用即报错）、飞书多维表同步、图形界面
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
