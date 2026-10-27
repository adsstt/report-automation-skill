# 可选目标模板与标准报表实施计划

## 目标

在不破坏现有目标模板流程的前提下，为无模板用户增加“源数据 + 业务目标 → 周期建议与确认 → 标准三 Sheet 报告”的路径。

## 顺序

1. 为无模板 infer CLI 编写失败测试：业务目标输入、周期建议、周期未确认状态、恰好三个 Sheet 定义。
2. 扩展 profile schema，新增向后兼容的可选 `report_intent`。
3. 扩展 `infer_mappings.py`：增加 `--business-goal` 与 `--standard-output`，保留旧命令行为。
4. 为 `create_standard_template.py` 编写失败测试：未选周期完全阻断、DRAFT 标识、正式三 Sheet。
5. 实现标准模板生成器：管理摘要、明细数据、趋势环比；保护源文件和已有工作簿。
6. 补端到端测试：确认后的 profile 能生成正式模板并由现有渲染流程填数；零分母和缺少对比期不伪造。
7. 更新 `SKILL.md`、`README.md`、schema 文档与 npm 包清单。
8. 跑 Python 单测、DSH 包测试、NovaFit 验收和全新无模板用户模拟。

## 兼容性约束

- 未传 `--standard-output` 时，infer 输出与当前目标模板流程保持一致。
- `report_intent` 为可选字段，已有 profile 无需迁移。
- 未显式确认时间周期时，不生成模板、草稿或正式报告。
- 模板生成器只创建新文件，不覆盖已存在文件，除非显式 `--force`。
- 引擎不增加平台条件分支。

## 完成定义

- 公共 CLI 测试和全部既有测试通过。
- NovaFit 有模板验收通过。
- 新建无模板模拟产品从 probe 到正式三 Sheet 报告通过。
- README 与 SKILL 能指导全新用户完成两条路径。
