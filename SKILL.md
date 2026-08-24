---
name: blueink-suite
description: >-
  蓝墨 BlueInk——证据驱动的汽车公关文案编辑决策系统。由一个 Claude Code 智能体完成逐轮访谈、写法确认、成稿与
  高风险事实复核；默认生成只加载一份快线指导，只在附件不足、来源冲突或真实反馈出现时读取条件指导。用于新闻稿、
  媒体供稿、邀请函、传播指引、核心信息、演讲稿、QA、社会化文案、视频脚本和串词。必须显式调用 /blueink-suite。
license: proprietary
disable-model-invocation: true
compatibility: Claude Code only. Supports macOS and Windows with Python 3.9 or newer.
metadata:
  author: 新蓝标数字 · 汽车事业群 · AI 内容中台
  version: 4.0.0
  created: 2026-08-22
  last_reviewed: 2026-08-24
  review_interval_days: 90
  maintainer: 汽车事业群 AI 内容中台 · 文案岗
  provenance: 蓝墨平台四层架构、汽车品牌历史稿件资产、一线文案岗反馈与真实 Claude Code 运行复盘
---

# /blueink-suite

## 目标

交付一篇老师能继续修改或提交的稿件，而不是展示一套复杂流程。先让老师确认真正会改变正文的判断，再写一次、核一次；
事实安全、单品牌隔离和来源可追溯不能因精简而取消。

## 不变边界

1. **显式启动。** 只响应 `/blueink-suite`；同名冲突时使用 `/blueink-suite:blueink-suite`。
2. **只有一个智能体。** 不调用 Agent、Task、后台智能体、独立 `claude` 进程或其他模型会话。成稿后的核验是同一上下文内的受限复核，不称为独立或盲评。
3. **一个工作空间只绑定一个品牌知识库。** 自主检索不能越过绑定根；老师显式提供并登记的附件可以位于根外。
4. **生成前必须确认写法。** 生成任务不允许零轮访谈。普通任务一到两轮；只有未解决的事实或来源硬冲突才逐轮追加。
5. **来源没有明确支持时，不写强事实。** `唯一、全部、普遍、均、最高` 等词只有在事实原子的比较范围和允许词中出现时才能使用；否则改成有边界的分析判断。
6. **默认只有四份运行产物。** `run.json`、`draft.md`、`verify.json`、`delivery.md`。扩展研究仍写入 `run.json`；真实反馈才增加 `feedback.json`。

模型和 effort 由用户在当前 Claude Code 会话中自行选择；BlueInk 不指定、不切换，也不把模型选择写成提速标准。

## 第一步：登记本次证据

先解析 Python、技能根和项目根。下文 `$BLUEINK` 代指已经在当前电脑试跑过的：

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

它定义逐轮访谈、动态写法选择、最多 12 条事实原子、正文、高风险句复核和四份产物。不要再读取旧的阶段协议或把策略、程序、回执拆成多份文件。

## 条件路由

| 条件 | 读取 |
|---|---|
| 附件不足、来源冲突、时效问题、需要知识库或官方补充 | `${CLAUDE_PLUGIN_ROOT}/references/research.md` |
| 老师给出真实修改稿、选择或明确反馈 | `${CLAUDE_PLUGIN_ROOT}/references/feedback.md` |
| Python、路径、绑定、索引、运行记录或审计失败 | `${CLAUDE_PLUGIN_ROOT}/references/troubleshooting.md` |

未命中条件时不要读取这些文件。

## 结构化写入

模型不得直接写正式 JSON。方向确认后，把结构化内容通过标准输入交给脚本即时校验并原子写入：

```bash
$BLUEINK save --run "<run_id>" --kind decision <<'BLUEINK_JSON'
{...}
BLUEINK_JSON
```

写完正文后同样保存核验：

```bash
$BLUEINK save --run "<run_id>" --kind verify <<'BLUEINK_JSON'
{...}
BLUEINK_JSON
```

正文是唯一允许直接写入的运行文件：

```text
.blueink/runs/<run_id>/draft.md
```

最后执行 `close`。脚本根据 `draft.md + verify.json` 生成 `delivery.md` 后归档：

```bash
$BLUEINK close --run "<run_id>"
```

## 禁止动作

- 不把“信息已经足够”当成跳过方向确认的理由。
- 不提供固定的“专业／活泼／克制”风格菜单；写法选项必须来自本次事实、受众和传播目标。
- 不为凑 A/B 只换标题、措辞或语气。只有一条方向成立时，展示唯一推荐、说明没有备选的原因，并等待确认。
- 不让核验阶段重新写全文。只检查数字、日期、主体、比较范围、因果和时效等高风险句；最多返回局部修改建议。
- 不把同一上下文内的自检包装成独立审查。
- 不因回执、归档或审计格式继续占用老师等待时间；结构错误在 `save` 时处理。
- 不设置模型规格、硬性生成时限或自动超时终止；只记录实际运行数据，后续按真实问题调优。

## 交付

业务侧只看到：正文、极短核对结论、真正使用的来源。不要展示运行文件名、阶段轨迹、读过的规则或已通过检查项。

## 能力边界

负责文案生成与来源核对；不做 Word 排版、字体、配图、品牌文档模板、改稿编排或版本回退。反馈只形成有条件的候选证据，不自动改方法论或源知识库。
