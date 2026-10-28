# 全新用户实用性验收矩阵

这组验收不是直接调用仓库脚本。它会先把当前版本打成 npm tarball，安装到隔离目录，
再通过 DSH 适配层同步到一个全新的用户技能目录，最后从该安装目录执行三套场景。

| 场景 | 平台组合 | 模板 | 用户知识 | 关键验收 |
|---|---|---|---|---|
| AtlasPay | TikTok + AppsFlyer | 有 | 熟悉字段 | AI 列出 P0，确认后按模板输出花费、注册、KYC、FTD |
| BloomCart | Meta + Google + Adjust | 无 | 完全小白 | 未选周期先阻断；标准三 Sheet 展示全部平台 + 平台分项、真实日期明细、完整漏斗与效率指标 |
| LumaGame | Google + AppsFlyer | 有 | 不懂归因字段 | Activity/LTV 多候选必须阻断；确认 Activity 后输出注册、KYC、FTD |

运行：

```bash
python evals/usability-matrix/run_matrix.py
```

产物写入本目录的 `runtime/`，其中 `usability_result.md` 汇总安装、门禁、数值和工作簿结果。

