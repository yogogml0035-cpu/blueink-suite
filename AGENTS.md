# AGENTS.md — blueink-suite

供只读 `AGENTS.md` 的工具使用（Codex CLI、Augment、Continue.dev、Zed 等）。**这是一份导航文件，不是操作手册。** 方法论在 `SKILL.md`，运行细节在 `references/`。

## 这是什么

蓝墨 BlueInk——**证据驱动的汽车公关文案编辑决策系统**。

它不是汽车文案规则库。每次任务重新判断事实边界、传播主线、素材取舍和信息权重，动态形成一份只对本次有效的"写作程序"，再交给互相隔离的写作与审查职责执行；反馈修正的是判断条件与证据权重，不是不断增加固定规则。

## 怎么触发

只认显式 `/blueink-suite`。`SKILL.md` 与 `commands/blueink-suite.md` 两处都设了 `disable-model-invocation: true`，从机械上关掉自动触发。不保留 `/blueink` 别名。

收到调用后第一句回复必须是启动回执行（`BlueInk 已启动 · run-id: …`）。看不到这一行就是没走技能。

## 跨阶段底线

1. 只由显式 `/blueink-suite` 启动。
2. 一个工作空间一个品牌、一位文案老师，源知识库只读；换品牌或换人就换项目。
3. 主智能体只对话、路由、裁决——不写正文，不在最后偷偷重写，不替子智能体补事实。
4. 事实必须有来源；冲突显式上交老师裁决，不静默选边。
5. 反馈先成为带条件的候选证据，一次修改不直接变成规则。
6. 老师本次明确提供的附件就是本次证据，绑定根只约束系统自主检索；附件必须登记。

## 第一件事：本次的证据从哪来

三种情形，判断顺序固定：

| 情形 | 怎么做 |
|---|---|
| 老师给了明确的参考文件路径 | `open --attach <绝对路径>`，不需要知识库，也不需要绑定 |
| 项目还没绑定知识库 | 访谈里一次问一件，问出品牌、老师、知识库目录，再 `bind`；老师还没有目录时加 `--create` 建骨架 |
| 已绑定，但本次品牌可能不是绑定的那个 | `check-brand --brand <本次品牌>`；不匹配时把三条出路交给老师，不自己选边 |

品牌名一律传给 `open --brand`，它会在开启运行时再挡一次。

## 访谈的停止判据

五个维度：**事实安全、传播主线、信息权重、表达边界、交付可行性**。下一问的答案仍有现实可能改变其中任一项时继续追问，否则记录剩余假设进入写作。它们是判据，不是问卷。

每轮恰好一个问题；问前先读已有回答、看附件、查索引。停止理由必须指名评估过哪一维，不显示信心百分比——两条都由审计 A3 机械守着。

## 命令入口

确定性工作全部收在一个入口里，纯标准库，无第三方依赖，底线 Python 3.9：

```bash
python3 scripts/blueink.py doctor        # 一条命令看清当前状态
python3 scripts/blueink.py bind … [--create]     # 绑定单品牌单老师工作空间
python3 scripts/blueink.py check-brand --brand … # 本次品牌与知识库是否匹配
python3 scripts/blueink.py index         # 旁路增量索引
python3 scripts/blueink.py open --mode 生成 --brand … [--attach <绝对路径>]…
python3 scripts/blueink.py audit --run <run_id>
python3 scripts/evolve.py                     # 一次跑完全部质量门
```

`blueink.py --help` 列出全部子命令；各子命令的语义在下表对应的 reference 里。它是**状态与记账层，不是管线**：没有"跑完就出稿"的命令。

## 按需读取

| 要做什么 | 读 |
|---|---|
| 方法论内核、五条原则、决策主干、运行底线 | `SKILL.md` |
| 问出知识库路径或建一个、品牌不匹配、索引、附件登记、官方来源白名单、留存清理 | `references/workspace-and-index.md` |
| 逐轮访谈；下一个问题该问什么；还该不该继续问 | `references/interview-protocol.md` |
| 取证；三轨判据；冲突处置；什么时候才允许联网 | `references/evidence-tracks.md` |
| 交付物必须完成什么传播任务 | `references/category-boundaries.md` |
| **启动子智能体前必读**：六个角色、工具白名单、任务单与回执字段、返工规则 | `references/orchestration-protocol.md` |
| 核对卡、来源清单、A/B 呈现、结论口径 | `references/delivery-contract.md` |
| 反馈归因、置信度升降、记忆能否自动参与写作 | `references/conditional-memory.md` |
| **定位问题、审计结论读法、本地环境的反直觉事实、留存策略** | `references/troubleshooting.md` |
| 某个新增设计是否违背核心命题 | `references/methodology-core.md` |

工程记录：`DESIGN_NOTES.md`（每条机械约束为什么存在、做不到什么）、`DECISIONS.md`（架构决策及被否掉的替代方案）、`EVOLUTION.md`（使用中捕获的纠错）、`CHANGELOG.md`。
