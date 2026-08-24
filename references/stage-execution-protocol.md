# 单智能体阶段执行协议

选择运行路径，以及进入证据、策略、成稿、核验、红队或反馈阶段前读这里。这份协议定义一个 Claude Code 智能体如何在同一会话中按需切换职责，并用运行产物保持可定位、可恢复。

## 一 · 运行模型

BlueInk 只有一个运行中的智能体：当前 `/blueink-suite` 会话里的主智能体。所有访谈内容、已读材料和工具都共享在同一个上下文中。

六份阶段指导文档由当前 `/blueink-suite` 会话按需读取：

| 阶段 | 何时进入 | 进入时读取 | 产物 |
|---|---|---|---|
| 编辑策略 | 每次生成；快线中先做轻量证据整理 | `references/stages/editorial-strategy.md` | `evidence.json`（快线）+ `strategy.json` |
| 成稿 | 每次生成 | `references/stages/writing.md` | `draft.md`、`write-receipt.json` |
| 来源核验 | 每次成稿后 | `references/stages/source-verification.md` | `verify.json` |
| 编辑红队 | 每次来源核验后 | `references/stages/editorial-red-team.md` | `adversary.json` |
| 证据研究 | 只有需要查询附件外资料或处理高影响证据问题 | `references/stages/evidence-research.md` | `evidence.json` |
| 反馈归因 | 只有老师给出真实修改或反馈 | `references/stages/feedback-attribution.md` | `feedback.json` |

**运行期间只使用当前 `/blueink-suite` 会话。** 阶段按顺序执行，每个阶段先落产物再继续；不启动后台角色进程，也不通过独立 CLI 会话执行阶段职责。

## 二 · 运行能力边界

阶段之间共享同一会话上下文和宿主工具。阶段指导通过输入合同收束当前判断依据，`read_paths` 和审计记录可观察的文件访问。

来源核验与编辑红队属于同一会话内的受限输入复核：来源核验只依据 `draft.md`、`evidence.json` 和 `program.json`；编辑红队只依据正文、传播任务和交付合同。交付物将这两项标记为复核结果。

运行产物包括 `evidence.json`、`strategy.json`、`program.json`、`draft.md`、`verify.json`、`adversary.json` 和 `feedback.json`。每份产物既是下一阶段的输入，也是恢复与问题定位的断点。

## 三 · 阶段输入边界

上下文共享不等于每个阶段可以重新使用所有信息。进入阶段后，只把下表中的内容当作当前判断依据：

| 阶段 | 当前允许依赖 | 当前不得新增 |
|---|---|---|
| 证据研究 | 访谈、登记附件、`kb_root`、官方来源白名单 | 跨根自主检索、文章结构、正文 |
| 编辑策略 | `evidence.json`；或快线中的登记文本附件、访谈结论、品类边界 | 新检索、附件外新事实、完整正文 |
| 成稿 | `program.json` 与 `authorized_reads` | 新检索、重选素材、补事实、换主线 |
| 来源核验 | `draft.md`、`evidence.json`、`program.json` 指名的来源 | `strategy.json` 理由、新搜索、替稿件补来源、直接改稿 |
| 编辑红队 | `draft.md`、传播任务、交付合同 | `strategy.json` 理由、`program.json`、事实补查、直接改稿 |
| 反馈归因 | 初稿、老师修改稿或原话、`program.json`、品牌记忆 | 推测未说明的动机、自动改方法论或源库 |

这张表是**推理输入合同**。已经出现于会话但不在当前允许输入中的内容，不作为本阶段结论的依据。

## 四 · 怎么切换阶段

每次进入一个阶段，按同一动作执行：

1. 确认所选路径要求的上游产物已存在且 JSON 可解析；快线进入编辑策略时允许尚无 `evidence.json`，但必须先在本阶段写出它；`status` 为 `blocked` 时停止，不绕过。
2. 只读取本阶段指导文档；不要一次把六份文档全部加载进上下文。
3. 在内部明确当前阶段、允许输入、禁止动作和目标产物。不要把这份内部切换卡输出给老师。
4. 完成本阶段后先写产物，再进入下一阶段。会话中断时只从最后一个缺失或不可用的产物恢复。

内部切换卡使用下面的最小结构：

```text
stage: evidence-research | editorial-strategy | writing | source-verification | editorial-red-team | feedback-attribution
run_id: <当前运行>
guide: <本阶段指导文档绝对路径>
inputs: <当前允许依赖的产物或附件>
forbidden: <本阶段禁止动作>
expect: <本阶段目标产物绝对路径>
```

切换卡用于收束当前阶段的注意力，不向用户展示。`skill_root`、`project_root`、`kb_root` 与 `cli` 使用当前机器解析出的真实路径；不要依赖技能目录作为工作目录。

## 五 · 回执信封

阶段 JSON 使用以下公共字段：

```json
{"role": "...", "run_id": "...", "task_id": "...", "status": "...", "read_paths": []}
```

`role` 是审计器使用的稳定阶段标识：

| 阶段 | `role` |
|---|---|
| 证据研究 | `evidence-researcher` |
| 编辑策略 | `editorial-strategist` |
| 成稿 | `professional-writer` |
| 来源核验 | `source-verifier` |
| 编辑红队 | `editorial-adversary` |
| 反馈归因 | `feedback-attributor` |

每份回执都写 `read_paths`。空数组表示本阶段没有额外打开文件；缺字段则无法审计。路径必须是当前平台的绝对路径，或相对 `kb_root` 的路径，不许缩写。运行目录里的上游产物可以直接记录为绝对路径。

回执上限如下：

| 回执 | 最小必要内容 | 上限 |
|---|---|---|
| `evidence.json` | `facts`、`timeline`、`conflicts`、`gaps`、`read_paths` | facts ≤ 25 条；每条陈述 ≤ 2 行 |
| `strategy.json` | 推荐主线、真实备选、素材取舍、信息预算 | ≤ 220 行；`why` ≤ 5 行 |
| `program.json` | `authorized_reads`、`material_plan.discarded`、`assumptions`、主线、信息预算 | ≤ 120 行 |
| `write-receipt.json` | `read_paths`、`style_refs_used`、`deviations`、`missing_facts` | ≤ 40 行 |
| `verify.json` | 问题事实句、`coverage`、`cross_brand`、`redline_hits` | 全部通过时 `claims` 可为空 |
| `adversary.json` | `attacks`、`missed_stronger_line`、检查面 | 攻击 ≤ 6 条，按严重度排序 |
| `feedback.json` | 条件化候选记忆 | 候选 ≤ 8 条 |

超限时舍弃低影响内容，不复制长篇原文。

`status` 口径：

| status | 含义 | 动作 |
|---|---|---|
| `ok` / `pass` / `sound` | 正常完成 | 进入下一阶段 |
| `partial` | 完成但有缺口 | 高影响缺口回访谈，低影响缺口显式保留 |
| `objected` | 对上游有实质异议 | 回上游阶段裁决，不自行换方向 |
| `must_fix` / `weak` | 有可定位问题 | 只返工问题所在阶段 |
| `broken` | 方向性问题 | 回编辑策略阶段 |
| `blocked` | 缺关键输入 | 补输入后重跑当前阶段，不硬写 |

## 六 · 先路由，再执行

### 封闭附件快线（默认）

同时满足以下条件时走快线：

- `evidence_boundary: attachments`，且登记附件正文可读；
- 附件已经足以支撑本次交付；
- 没有会改变主线的来源冲突、时效问题或来源缺口；
- 不需要知识库检索、官方补充或额外风格样本。

快线不加载 `evidence-research.md`。编辑策略阶段先按附件形成轻量 `evidence.json`，冻结事实边界，再在同一阶段形成 `strategy.json`。事实清单、冲突记录和来源映射均写入 `evidence.json`。

### 扩展证据路径（条件触发）

出现以下任一情况才进入扩展证据研究：没有登记附件、任务明确依赖绑定知识库、附件正文不可读、多个来源存在高影响冲突、事实具有明显时效性、老师要求官方补充、关键来源缺失，或缺少的品牌风格样本确实会改变成稿。

不要因为“可能有用”升级路径。进入扩展路径后只围绕已命名的缺口检索；缺口闭合即停止。

## 七 · 两条生成主干

```text
封闭附件快线：
0  open → 1 访谈 → 2 编辑策略（先写 evidence.json，再写 strategy.json）
→ 3 program.json → 4 成稿 → 5 来源核验 → 6 编辑红队 → 7 交付与归档

扩展证据路径：
0  open → 1 访谈 → 2 证据研究（evidence.json）→ 3 编辑策略
→ 4 program.json → 5 成稿 → 6 来源核验 → 7 编辑红队 → 8 交付与归档

真实反馈后：反馈归因 → 记忆晋级
```

两条路径都不是检查清单：已有可靠产物可以跳过；事实问题优先回到快线的轻量证据整理，只有需要新增资料时才进入扩展证据研究；主线问题回编辑策略，执行偏差回成稿。不要重跑整条链，也不要把阶段表展示给老师。

`program.json` 的 `authorized_reads`、`material_plan.discarded`、`assumptions` 必须写全。`delivery.md` 记录业务侧实际收到的内容，不能省。

交付完成并写入 `delivery.md` 后，**Run（运行）** `$BLUEINK close --run <run_id>` 归档本次运行。

## 八 · A/B 的真实边界

单智能体不能产出两个互相不可见的策略实例，也不能对自己写的两稿做真正盲评。需要 A/B 时：

1. 在 `strategy.json` 里先写推荐主线；
2. 指定一个会改变传播效果的相反变量，再构造备选主线；
3. 如果两条主线只是措辞不同，取消 A/B；
4. 确需成文时顺序写 `draft-a.md` 与 `draft-b.md`，分别落回执和核验；
5. 编辑红队只报告差异、适用目的和代价，不把比较称为独立盲评。

A/B 结果标记为同一执行者的竞争性比较，并明确两条主线的适用目的与代价。

## 九 · 恢复与失败

各阶段顺序执行并直接写入运行目录。不要用 `sleep`、文件轮询或后台进程模拟阶段调度。

恢复时用 `doctor` 和运行目录判断最后一个可用产物：存在且可解析就复用；缺失、损坏或 `blocked` 只重做当前阶段。`blueink.py stage --run <run_id> --to <阶段编号>` 仍是可选标记，实际产物是事实来源。

## 十 · 生成与改稿的边界

生成内部可以按 `status` 局部回退。老师看稿后的修改意见不自动触发整条链重跑；它进入反馈归因与学习外循环。BlueInk 不建设改稿编排、版本回退或阶段回退 UI。
