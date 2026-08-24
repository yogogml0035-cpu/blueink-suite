# 单智能体阶段执行协议

进入证据、策略、成稿、核验、红队或反馈阶段前读这里。这份协议定义一个 Claude Code 智能体如何在同一会话中按阶段切换职责，并用运行产物保持可定位、可恢复。

## 一 · 运行模型

BlueInk 只有一个运行中的智能体：当前 `/blueink-suite` 会话里的主智能体。所有访谈内容、已读材料和工具都共享在同一个上下文中。

六份阶段指导文档不是 Agent 定义，不会注册成子智能体：

| 阶段 | 进入时读取 | 产物 |
|---|---|---|
| 证据研究 | `references/stages/evidence-research.md` | `evidence.json` |
| 编辑策略 | `references/stages/editorial-strategy.md` | `strategy.json` |
| 成稿 | `references/stages/writing.md` | `draft.md`、`write-receipt.json` |
| 来源核验 | `references/stages/source-verification.md` | `verify.json` |
| 编辑红队 | `references/stages/editorial-red-team.md` | `adversary.json` |
| 反馈归因 | `references/stages/feedback-attribution.md` | `feedback.json` |

**不得调用 Agent、Task、子智能体或 `claude --agent`。** 不启动后台角色进程，不并行生成阶段回执，也不通过独立 CLI 会话模拟角色。中转站是否支持多智能体不再属于运行前提。

## 二 · 什么被保留，什么不再声称

保留的是六种不能互相替代的责任、上游输入、输出格式、局部返工规则和产物断点。已有 `evidence.json`、`strategy.json`、`program.json`、`draft.md`、`verify.json`、`adversary.json`、`feedback.json` 契约继续使用，状态层和审计器无需重造。

不再声称三件事：

- 不声称阶段之间有独立上下文；同一执行者看过的内容无法真正遗忘。
- 不声称来源核验或编辑红队是独立评审；它们是同一执行者按受限证据重新检查。
- 不声称工具白名单由宿主强制；阶段禁止项是执行合同，`read_paths` 和审计只能事后发现可见越界，不能证明未记录的工具调用从未发生。

这不是措辞上的降级，而是能力边界。单智能体能可靠模拟职责切换和产物门禁，不能模拟认知独立性。

## 三 · 阶段输入边界

上下文共享不等于每个阶段可以重新使用所有信息。进入阶段后，只把下表中的内容当作当前判断依据：

| 阶段 | 当前允许依赖 | 当前不得新增 |
|---|---|---|
| 证据研究 | 访谈、登记附件、`kb_root`、官方来源白名单 | 跨根自主检索、文章结构、正文 |
| 编辑策略 | `evidence.json`、访谈结论、登记附件、品类边界 | 新检索、新事实、完整正文 |
| 成稿 | `program.json` 与 `authorized_reads` | 新检索、重选素材、补事实、换主线 |
| 来源核验 | `draft.md`、`evidence.json`、`program.json` 指名的来源 | `strategy.json` 理由、新搜索、替稿件补来源、直接改稿 |
| 编辑红队 | `draft.md`、传播任务、交付合同 | `strategy.json` 理由、`program.json`、事实补查、直接改稿 |
| 反馈归因 | 初稿、老师修改稿或原话、`program.json`、品牌记忆 | 推测未说明的动机、自动改方法论或源库 |

这张表是**推理输入合同**，不是上下文清除或权限隔离。已经看过上游理由时，不假装没看过；只是不把它当成当前阶段的证据。

## 四 · 怎么切换阶段

每次进入一个阶段，按同一动作执行：

1. 确认上一阶段要求的产物已存在且 JSON 可解析；`status` 为 `blocked` 时停止，不绕过。
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

它是当前智能体的注意力切换，不是发给另一个智能体的任务单。`skill_root`、`project_root`、`kb_root` 与 `cli` 仍须使用当前机器解析出的真实路径；不要依赖技能目录作为工作目录。

## 五 · 回执信封与兼容字段

阶段 JSON 继续带以下字段：

```json
{"role": "...", "run_id": "...", "task_id": "...", "status": "...", "read_paths": []}
```

`role` 是既有审计格式中的稳定阶段标识，不表示另有一个 Agent 实例：

| 阶段 | `role` |
|---|---|
| 证据研究 | `evidence-researcher` |
| 编辑策略 | `editorial-strategist` |
| 成稿 | `professional-writer` |
| 来源核验 | `source-verifier` |
| 编辑红队 | `editorial-adversary` |
| 反馈归因 | `feedback-attributor` |

每份回执都写 `read_paths`。空数组表示本阶段没有额外打开文件；缺字段则无法审计。路径必须是当前平台的绝对路径，或相对 `kb_root` 的路径，不许缩写。运行目录里的上游产物可以直接记录为绝对路径。

回执上限保持不变：

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

## 六 · 标准主干

```text
0  运行开启      blueink.py open                         → meta.json
1  访谈          当前智能体按访谈协议                    → interview.json
2  证据研究      读取 evidence-research.md               → evidence.json
3  编辑策略      读取 editorial-strategy.md              → strategy.json
4  写作程序      当前智能体按策略合成                    → program.json
5  成稿          读取 writing.md                         → draft.md + write-receipt.json
6  来源核验      读取 source-verification.md             → verify.json
7  编辑红队      读取 editorial-red-team.md              → adversary.json
8  交付          正文 + 核对卡 + 来源清单                → delivery.md
9  运行归档      blueink.py close                        → index.json 摘要
── 老师给出真实反馈后 ──
10 反馈归因      读取 feedback-attribution.md            → feedback.json
11 记忆晋级      按条件化记忆判据                        → .blueink/learning/
```

它仍是有向主干，不是十一项清单：已有可靠产物可以跳过；时效、冲突、竞争主线和高影响推断出现时才触发分支；事实问题回 2，主线问题回 3，执行偏差回 5。不要重跑整条链，也不要把阶段表展示给老师。

`program.json` 的 `authorized_reads`、`material_plan.discarded`、`assumptions` 必须写全。`delivery.md` 记录业务侧实际收到的内容，不能省。

## 七 · A/B 的真实边界

单智能体不能产出两个互相不可见的策略实例，也不能对自己写的两稿做真正盲评。需要 A/B 时：

1. 在 `strategy.json` 里先写推荐主线；
2. 指定一个会改变传播效果的相反变量，再构造备选主线；
3. 如果两条主线只是措辞不同，取消 A/B；
4. 确需成文时顺序写 `draft-a.md` 与 `draft-b.md`，分别落回执和核验；
5. 编辑红队只报告差异、适用目的和代价，不把比较称为独立盲评。

这个方案保留真实竞争，放弃虚假的独立性。

## 八 · 恢复与失败

同一智能体顺序执行，不存在等待子智能体返回、后台超时或中转拒绝 Agent 参数的问题。不要用 `sleep` 或轮询模拟调度。

恢复时用 `doctor` 和运行目录判断最后一个可用产物：存在且可解析就复用；缺失、损坏或 `blocked` 只重做当前阶段。`blueink.py stage --run <run_id> --to <阶段编号>` 仍是可选标记，实际产物是事实来源。

## 九 · 生成与改稿的边界

生成内部可以按 `status` 局部回退。老师看稿后的修改意见不自动触发整条链重跑；它进入反馈归因与学习外循环。BlueInk 不建设改稿编排、版本回退或阶段回退 UI。
