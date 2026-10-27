# 可选目标模板与标准报表自动生成设计

日期：2026-10-27  
状态：已确认并实现

## 目标

让 `report-automation` 支持两种首次接入方式：

1. 用户提供目标模板时，继续按目标模板反推格式、对账并生成报告。
2. 用户没有目标模板时，根据源数据与用户提供的业务目标，经过时间周期选择和数字口径确认后，自动生成第一版标准报告。

无模板模式必须保持现有安全原则：版式可以由 AI 建议，数字口径不得猜测；存在未解决 P0 时只能生成带明显标识的草稿，不能生成正式报告。

## 非目标

- 不让模型自动决定归因归属、候选事件字段、账户排除、时区、币种或精度。
- 不在数据处理引擎中增加 `if platform == ...` 一类平台分支。
- 不在首版中支持任意数量或任意名称的 Sheet。
- 不把口径与数据质量问题做成工作簿 Sheet；这些问题在对话中确认并写回 profile。
- 不改变已有目标模板用户的命令和结果。

## 双分支接入流程

### 有目标模板

```text
源数据 + 目标模板
→ Probe
→ Infer
→ 口径确认
→ 影子对账
→ 冻结 profile
→ 按目标模板生成报告
```

该分支保持现有行为，包括目标表头识别、格式核对、样例逐格对账、模板保护和历史追加。

### 无目标模板

```text
源数据 + 业务目标
→ Probe 源数据
→ AI 给出时间周期建议
→ 用户显式选择时间周期
→ AI 推荐 KPI 与数据粒度
→ 对话确认 P0 口径
→ 生成标准模板预览（待确认）
→ 用户确认版式
→ 冻结 profile
→ 生成正式三 Sheet 报告
```

AI 应根据源数据中的日期跨度、数据频率和业务目标提供时间周期建议。例如：

- 数据按日更新且只有数日：优先建议日报。
- 数据覆盖多个完整自然周：建议周报并说明本周对上周。
- 数据覆盖多个完整自然月：可以建议月报。
- 用户描述活动、投放阶段或特定事件窗口：建议自定义日期范围。

建议必须附带依据，但不能替用户完成选择。用户未显式选择日报、周报、月报或自定义日期范围时，阻断模板和正式报告生成。

## 业务目标与 KPI

支持四类入口：

- 拉新增长：安装、注册、KYC、首充等候选指标。
- 投放效率：花费、展示、点击、CTR、CVR、CPA、ROAS 等候选指标。
- 收入增长：收入、付费人数、客单价、ROI 等候选指标。
- 自定义目标：用户用自然语言描述，AI 从可用源字段中提出 KPI 候选。

AI 可以推荐 KPI，但只有在字段唯一、粒度一致且证据充分时才能标记为高置信候选。归因归属、同一事件多候选字段、账户排除、日期时区、币种和精度仍按 P0 协议向用户确认。

源数据不包含某个目标 KPI 时，从报表候选中移除，并在对话中说明缺失字段，不以零值代替。

## 标准工作簿

无模板首版固定生成三个 Sheet。

### 管理摘要

- 报告名称、产品、周期和生成时间。
- 用户确认的业务目标。
- 4–8 个核心 KPI 卡片。
- 本期值、上期值、绝对变化和变化率。
- 一张核心指标趋势图。
- AI 生成的简短数据结论；结论只能描述已验证数字，不能猜测业务原因。

### 明细数据

- 根据源数据可用粒度自动选择维度。
- 默认维度优先级：日期、账户、国家、Campaign、Ad Group、Ad。
- 包含已确认的原始指标和派生指标。
- 启用筛选、冻结表头并应用统一数字格式。
- 不把 Campaign、Ad Group、Ad 等不同粒度直接相加。

### 趋势环比

- 支持日报、周报、月报和自定义日期范围。
- 默认与上一等长周期对比。
- 展示本期、上期、绝对变化和变化率。
- 分母为零时显示空值或 `N/A`，不生成虚假百分比。
- 缺少上一周期数据时保留 Sheet，并明确显示“缺少对比期数据”。

## 草稿与正式报告

模板预览文件使用 `_DRAFT.xlsx` 后缀，三张 Sheet 顶部均显示黄色“待确认”提示。

时间周期是生成前的硬阻断条件。时间周期尚未由用户显式选择时，系统只输出带依据的周期建议，不生成模板、草稿或正式报告。

用户已选择时间周期后，存在以下任一情况时只能生成草稿：

- 存在未解决 P0。
- 数据关联覆盖率不足，但仍达到允许预览的最低阈值。
- profile 存在不影响结构预览的待确认项。

数据关联覆盖率低于最低阈值或 profile 基础校验失败时，不生成草稿或正式报告。

口径和版式均确认后，重新生成正式模板和正式报告。正式文件不带 `_DRAFT` 后缀，也不显示“待确认”提示。

## 配置契约

profile 新增可选的 `report_intent`：

```yaml
report_intent:
  primary_goal: acquisition
  goal_description: 提升注册到首充的转化效率
  period:
    type: weekly
    start: 2026-10-19
    end: 2026-10-25
    confirmed: true
    recommendation:
      suggested_type: weekly
      evidence:
        - 源数据覆盖两个完整自然周
  comparison:
    mode: previous_equal_period
  primary_kpis:
    - spend
    - registration
    - kyc
    - first_deposit
  dimensions:
    - date
    - account_id
    - country_code
```

`report_intent` 对已有 profile 为可选字段，因此目标模板模式保持向后兼容。

首次对话的选择结果可以先写入 `runtime/business-goal.yaml`，随后由 infer 流程写入 profile 的 `report_intent`，保证日常出报不再重复询问已确认内容。

## 组件与公开命令

### Probe

无模板时仅探源数据：

```bash
python scripts/probe_sources.py \
  --source-dir <源数据目录> \
  --out runtime/probe.json
```

### Infer

扩展 `infer_mappings.py`，支持业务目标与标准输出：

```bash
python scripts/infer_mappings.py \
  --probe runtime/probe.json \
  --business-goal runtime/business-goal.yaml \
  --standard-output \
  --out runtime/profile.draft.yaml
```

输出包含 `report_intent`、候选指标、P0 疑点以及三个标准 Sheet 的输出定义。

### 标准模板生成器

新增 `scripts/create_standard_template.py`：

```bash
python scripts/create_standard_template.py \
  --profile runtime/profile.confirmed.yaml \
  --out runtime/标准报表_DRAFT.xlsx \
  --draft
```

用户确认版式后生成正式模板：

```bash
python scripts/create_standard_template.py \
  --profile runtime/profile.confirmed.yaml \
  --out runtime/标准报表模板.xlsx
```

模板生成器只负责工作簿结构、表头、样式、格式和趋势图骨架，不重新实现指标计算。正式模板继续交给现有 `render_workbook.py` 填数。

## 错误与降级

- 时间周期未选择：给出有证据的周期建议并阻断后续生成。
- 业务目标过于宽泛：提供目标选项，不直接生成正式报表。
- KPI 无对应源字段：移除候选并解释缺失字段。
- KPI 有多个候选字段：产生 P0，确认前只允许草稿。
- 缺少对比期：生成当前周期数据；趋势环比显示缺少对比期，不填零。
- 关联覆盖率不足：阻断正式报告并列出未匹配键样例。
- 样本只有一天或一个国家：降低置信度并在对话中提示样本不足。
- 预览版式不满意：只修改 `report_intent` 或输出配置并重新生成，不修改数据引擎。

## TDD 公开测试边界

实现前确认以下公共接口为测试 seam：

1. `infer_mappings.py` CLI：无目标模板且提供业务目标时，输出包含三个 Sheet 定义的候选 profile。
2. `create_standard_template.py` CLI：时间周期未确认时返回非零退出码，且不生成任何工作簿。
3. `create_standard_template.py` CLI：周期已确认但存在未解决 P0，或显式 `--draft` 时，只生成带黄色提示的 `_DRAFT.xlsx`。
4. `create_standard_template.py` CLI：已确认 profile 生成恰好三个 Sheet 的正式模板。
5. 端到端 CLI：正式报告不含待确认标识，指标和上一等长周期环比正确。
6. 现有模板端到端流程：NovaFit 既有验收继续通过。
7. 文件保护：模板生成器不修改源数据或已有工作簿。

测试以命令行返回码、生成文件、工作簿公开结构和已知固定数值为断言，不测试内部私有函数。

## 验收标准

- 有模板用户的现有命令、profile 和输出不发生破坏性变化。
- 无模板用户必须先收到时间周期建议并完成显式选择。
- 用户未选择时间周期时不生成模板、草稿或正式报告，并返回可行动的周期选项。
- 无模板流程能生成包含管理摘要、明细数据、趋势环比的预览工作簿。
- 未解决 P0 时无法生成无草稿标识的正式报告。
- 用户确认后能生成正式三 Sheet 报告。
- 摘要、明细、环比三处使用同一套已确认指标口径。
- 缺少对比期或分母为零时不伪造数据。
- 全部既有回归测试与 NovaFit 端到端验收通过。

## 发布

功能完成并验收后：

1. 更新 `SKILL.md`、`README.md`、profile schema 和示例。
2. 在 GitHub `main` 发布代码。
3. 将 npm 版本从 `1.0.0` 提升为兼容性功能版本。
4. 从 npm 官方源全新安装，并分别验证有模板和无模板两条路径。
