---
name: blueink-suite
description: >-
  蓝墨 BlueInk——证据驱动的汽车公关文案编辑决策系统。由一个 Claude Code 智能体完成逐轮访谈、写法确认、成稿与
  高风险事实复核；默认生成只加载一份快线指导，只在附件不足、来源冲突或真实反馈出现时读取条件指导。用于新闻稿、
  媒体供稿、邀请函、传播指引、核心信息、演讲稿、QA、社会化文案、视频脚本和串词。必须显式调用 /blueink-suite。
license: proprietary
disable-model-invocation: true
disallowed-tools: Skill, Agent, Task
compatibility: Claude Code only. Supports macOS and Windows with Python 3.9 or newer.
metadata:
  author: 新蓝标数字 · 汽车事业群 · AI 内容中台
  version: 4.1.1
  created: 2026-08-22
  last_reviewed: 2026-08-25
  review_interval_days: 90
  maintainer: 汽车事业群 AI 内容中台 · 文案岗
  provenance: 蓝墨平台四层架构、汽车品牌历史稿件资产、一线文案岗反馈与真实 Claude Code 运行复盘
---

# /blueink-suite

## 目标

尽快交付一篇老师可以立即修改的完整初稿，而不是让老师等待后台结构化记录全部完成。
先确认真正会改变正文的判断，再一次成稿；初稿交给老师后正文归老师修改，当前智能体只做受限核验和局部建议。

## 不变边界

1. **显式启动。** 只响应 `/blueink-suite`；同名冲突时使用 `/blueink-suite:blueink-suite`。
2. **只有一个 Skill、一个智能体。** 显式启动后，不调用 Skill 工具，不加载其他 Skill、斜杠命令或品牌写作助手；也不调用 Agent、Task、后台智能体、独立 `claude` 进程或其他模型会话。成稿后的核验是同一上下文内的受限复核，不称为独立或盲评。
3. **一个工作空间只绑定一个品牌知识库。** 自主检索不能越过绑定根；老师显式提供并登记的附件可以位于根外。
4. **生成前必须确认写法。** 生成任务不允许零轮访谈。普通任务一到两轮；只有未解决的事实或来源硬冲突才逐轮追加。
5. **来源没有明确支持时，不写强事实。** `唯一、全部、普遍、均、最高` 等词必须在核验时检查比较范围；没有来源支持就改成有边界的分析判断。
6. **默认只有四份运行产物。** `run.json`、`draft.md`、`verify.json`、`delivery.md`。扩展研究仍写入 `run.json`；真实反馈才增加 `feedback.json`。

模型和 effort 由用户在当前 Claude Code 会话中自行选择；BlueInk 不指定、不切换。系统只记录实际阶段耗时，不设置硬时限、自动超时或质量降级。

## 第一步：登记本次证据

Claude Code 加载 Skill 时会显示 `Base directory for this skill`。把这个现成目录记为技能根；插件环境里优先使用 `${CLAUDE_PLUGIN_ROOT}`。不要再用 `find`、`ls` 或 `status` 定位技能，也不要预先检查 Python 版本；只有命令实际失败时才进入排障。

下文 `$BLUEINK` 代指：

```bash
<Python 3.9+> "${CLAUDE_PLUGIN_ROOT}/scripts/blueink.py" --project "${CLAUDE_PROJECT_DIR}"
```

### 老师给了明确附件

有附件时不要先跑 `status`、`bind`、`check-brand` 或 `index`，直接登记：

```bash
$BLUEINK open --mode 生成 --brand "<品牌>" --attach "<绝对路径>" [--attach "<绝对路径>"]
```

附件默认是本次封闭事实边界。只有高影响缺口、冲突或时效问题阻止成稿时才最小扩展；不要为“可能有用”展开整库检索。

### 没有附件

先读取工作空间状态。未绑定时只问知识库位置这一件事，再绑定和索引：

```bash
$BLUEINK status
$BLUEINK bind --brand "<品牌>" --kb "<知识库绝对路径>" [--create]
$BLUEINK index
```

已绑定时确认本次品牌并核对：

```bash
$BLUEINK check-brand --brand "<本次品牌>"
$BLUEINK open --mode 生成 --brand "<本次品牌>"
```

品牌不匹配立即停止，把“品牌写错、换项目、只用指定附件”三条出路交给老师，不自行选边。

## 默认生成

开启运行后只读取：

```text
${CLAUDE_PLUGIN_ROOT}/references/generate.md
```

它定义逐轮访谈、轻量方向保存、可修改初稿交付和初稿后的受限核验。不要再读取旧的阶段协议或把策略、程序、回执拆成多份文件。

## 条件路由

| 条件 | 读取 |
|---|---|
| 附件不足、来源冲突、时效问题、需要知识库或官方补充 | `${CLAUDE_PLUGIN_ROOT}/references/research.md` |
| 老师给出真实修改稿、选择或明确反馈 | `${CLAUDE_PLUGIN_ROOT}/references/feedback.md` |
| Python、路径、绑定、索引、运行记录或审计失败 | `${CLAUDE_PLUGIN_ROOT}/references/troubleshooting.md` |

未命中条件时不要读取这些文件。

## 初稿优先写入

明确附件的普通生成不在成稿前整理事实原子和完整编辑决策。方向确认后，先把老师原话和选中方向轻量写入 `run.json`：

```bash
$BLUEINK save --run "<run_id>" --kind decision <<'BLUEINK_JSON'
{...}
BLUEINK_JSON
```

随后直接写 `draft.md`，立即执行：

```bash
$BLUEINK handoff --run "<run_id>"
```

`handoff` 会展示完整初稿、记录方向确认到初稿的实际耗时和正文哈希。此刻起正文归老师修改，当前智能体不得再写、编辑或覆盖 `draft.md`。

初稿展示后继续复核高风险句，只保存发现的问题；没有问题时 `issues` 为空：

```bash
$BLUEINK save --run "<run_id>" --kind verify <<'BLUEINK_JSON'
{...}
BLUEINK_JSON
```

最后执行 `close`。脚本只在正文仍与核验版本一致时生成 `delivery.md`、直接展示交付内容并归档：

```bash
$BLUEINK close --run "<run_id>"
```

`close` 的命令输出可能被折叠，不能代替面向老师的最终交付。最终回复的第一个可见区块必须是
`delivery.md` 的完整内容，不得在正文前加“稿件已完成”、文件路径、撰写思路或质检报告。归档路径只能在
全文之后作为附注；不得只报路径、摘要，也不得要求老师另行打开文件才能看到稿件。

## 禁止动作

- 不把“信息已经足够”当成跳过方向确认的理由。
- 不调用 Skill 工具，不加载其他 Skill、斜杠命令或品牌写作助手；更具体的品牌 Skill 也不能替代本运行合同。
- 不提供固定的“专业／活泼／克制”风格菜单；写法选项必须来自本次事实、受众和传播目标。
- 不为凑 A/B 只换标题、措辞或语气。只有一条方向成立时，展示唯一推荐、说明没有备选的原因，并等待确认。
- 不让核验阶段重新写全文。只检查数字、日期、主体、比较范围、因果和时效等高风险句；最多返回局部修改建议。
- 不在 `handoff` 后自动修改正文；即使发现问题，也只给出原句、原因和建议替换句。
- 不把同一上下文内的自检包装成独立审查。
- 不因事实原子、回执、归档或审计格式推迟初稿；扩展研究命中时才补完整事实与决策。
- 不设置模型规格、硬性生成时限或自动超时终止；只记录实际运行数据，后续按真实问题调优。

## 交付

初稿阶段只展示可修改正文和它的编辑路径；核验完成后的最终回复直接展示完整正文、极短核对结论和
真正使用的来源。除此之外不要展示阶段轨迹、读过的规则或已通过检查项。

## 能力边界

负责文案生成与来源核对；不做 Word 排版、字体、配图、品牌文档模板、改稿编排或版本回退。反馈只形成有条件的候选证据，不自动改方法论或源知识库。

## Gotchas

- Claude Code 直接加载 Skill 时 `${CLAUDE_PLUGIN_ROOT}` 可能为空，但加载结果已经给出技能 `Base directory`；使用它，不要重新搜索安装目录。
- 已给明确附件时 `status` 会把正常任务带回知识库绑定分支；直接 `open --attach`。
- `handoff` 是正文所有权切换点。之后核验只写 `verify.json` 和建议，不再修改 `draft.md`。
