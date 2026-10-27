# 疑点协议（ambiguity protocol）

报表自动化的成败不在"能不能算出来"，而在**该问人的时候有没有问**。
猜错口径的代价是客户数字错，不是样式丑。

## 三级分类

| 级别 | 判据 | 处理方式 |
|---|---|---|
| **P0 阻断** | 不解决算不出正确数字 | 必问；未解决时 `generate_report.py` / `render_workbook.py` 拒绝运行 |
| **P1 数值** | 能出稿，但个别格子对不上 | 出稿时附差异清单，允许带疑点交付 |
| **P2 展示** | 只影响样式、标题、精度 | 按建议值默认，标注可改 |

P0 的典型场景：

- 归因口径归属（投放平台 vs 归因平台谁说了算）
- 同一事件存在多个候选字段（增量/累计、去重实体、版本后缀）
- 账户排除名单
- 日期与时区口径（报表日、报表点位、周窗口边界）
- 跨层混加（campaign/adgroup/ad 三层不能相加）
- 关联覆盖率不足（低于阈值说明关联键错了）

## 五类永远不许默认猜

1. 归因口径归属
2. 同一事件多候选字段的选择
3. 账户排除名单
4. 日期与时区口径
5. 金额与百分比精度

这五类即使"看起来很明显"，也要列入疑点清单走确认流程。

## 每条疑点的固定结构

```
问题：    一句问句，包含选项或范围
证据：    我看到了什么（源数据统计 / 样例反解结果 / 官方文档原文与 URL）
建议值：  我的倾向 + 理由
影响：    确认后会影响哪些格子/指标，猜错的后果量级
写回：    确认后写进 profile 的哪个字段
```

示例：

> **问题**：注册数用哪一份？
> **证据**：TikTok 导出有「去重注册次数（应用）」，AppsFlyer 有
> `Unique users ltv days cumulative appsflyer signup_success`；同一天前者 504、后者 69。
> **建议值**：用 AppsFlyer（与历史报表一致，且归因口径统一）。
> **影响**：若搞反，注册成本从约 24 USD 变成约 3 USD，所有 CPA 与转化率全错。
> **写回**：`metrics.注册.from`

## 什么时候必须联网核验

出现下列任一情况，检索官方文档并在证据里写 **URL + 原文摘录**，不许凭印象断言：

- 目标格式需要、但 `references/platforms/` 里没有的字段
- 用户给的字段名与官方不一致
- 源文件出现 catalog 标记为 `unverified` 的字段
- 平台口径可能已变更（保留期、回传窗口、去重规则）

若官方文档拿不到（页面 403、未收录），**如实说明拿不到**，把结论降级为"低置信待核实"，不要伪造引用。

## 什么时候可以不问（P2 默认值）

- 纯展示：数字格式位数、列宽、标题措辞、单位
- 有明确行业惯例且不影响数值的：表头顺序（若按列名匹配写入）
- 用户已明确指定过的：同一 profile 里已确认过的口径不再重复问

## 疑点写完放哪

写进 profile 的 `ambiguities[]`：

```yaml
ambiguities:
  - id: p0_join_keys
    question: "tiktok × appsflyer 在广告粒度的关联键是什么？"
    evidence: "TikTok 侧同时有 ad_id 与 creative_id；AppsFlyer 只有 Ad ID"
    suggested: "creative_id ↔ Ad ID（历史产物验证过）"
    level: P0
    status: pending      # pending | confirmed | rejected
    decided_by: user
    written_to_rule: source_plan.joins[0].keys
```

`level: P0` 且 `status: pending` 会让校验器直接阻断出稿——这是"没问清就不许出数"的机制保证。
