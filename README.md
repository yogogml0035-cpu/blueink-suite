# blueink-suite

**证据驱动的汽车公关文案编辑决策系统。**

它不是一个规则库。它根据当前任务重新判断事实边界、传播主线、素材取舍和信息权重，动态形成一份只对本次有效的"写作程序"，再交给互相隔离的写作与审查职责执行。

```
传播任务 → 证据边界 → 编辑判断 → 本次写作程序 → 成稿 → 双重审查 → 交付与学习
```

这七个是**有前后因果依赖的责任节点，不是七个必须逐项执行的步骤**：已有可靠结果的节点跳过，条件分支只在真的出现时效／冲突／竞争主线／高影响推断时触发，发现问题只局部回退。

## 它解决什么

一线文案岗有七个反复出现的问题。它们看起来是七件事，其实是同一个前提性错误的七种表现——**把一线素材当成了可直接执行的规则**。素材不是规则，是**证据**：证据有强度、有时效、有适用范围，只有被本次任务命中才参与判断。

| 问题 | 这套系统怎么处理 |
|---|---|
| 全量加载语料撑爆上下文 | 技能本体不含任何品牌语料。分层按需加载，一次任务只加载十几个文件量级的相关切片 |
| 多品牌语料交叉感染 | 一个工作空间只绑定一个品牌，索引只扫描一个检索根。前提是该根本身只含这个品牌，且没有用 `--force` 绕过集合层提示 |
| 知识库太杂、没人愿意整理 | `bind --create` 可显式创建空骨架；绑定后的索引与写作流程只读源库。低置信度文件保留多个候选标签 |
| 固定结构限制创作 | 品类只规定必须完成的传播任务和边界，不规定段落顺序 |
| 自检只查禁词，缺来源标注 | 必须回答"这句话依据什么、观点从哪来、舍弃了什么"，交付极短核对卡 |
| 出问题不知道在哪 | 六个责任型角色各留任务单与回执，`audit` 直接指出违约在哪个文件的哪个字段 |
| 知识库里的固定模板被当经验用 | 带 `SKILL.md` 的目录整棵子树默认不参与检索——按目录判定，模板不在入口文件里 |

## 安装

需要 Python 3.9 或更高。**无第三方依赖**——文案老师不用装任何包，`requirements.txt` 里把这条写成了显式声明。YAML 读写用的是 `scripts/miniyaml.py` 里一个刚够用的子集实现，就是为了不引入 PyYAML。

底线不是一句口头承诺：`scripts/self_check.py --compat` 会静态扫描全部脚本里高于 3.9 的 stdlib API 与语法，并在本机存在 3.9 解释器时用它真跑一遍状态层与管线检查。**一个不被执行的检查器等于不存在。**

### Claude Code marketplace（推荐，macOS 与 Windows 相同）

```bash
/plugin marketplace add https://github.com/yogogml0035-cpu/blueink-suite
/plugin install blueink-suite@blueink-suite
```

插件根级 `SKILL.md` 是当前 Claude Code 官方支持的单 Skill 布局；根级 `agents/` 会随插件自动注册六个子智能体。安装后如果当前会话提示需要重载，运行 `/reload-plugins`，然后确认 `/blueink-suite` 出现在命令列表中。若已有其它插件占用同名裸命令，使用规范化入口 `/blueink-suite:blueink-suite`，并让 `open --started-via` 如实记录它；不要误调另一个同名命令。

**子智能体不是可选附件。** 写作者、策略师、核验员、反方、归因员都没有检索工具；证据研究员在 macOS 可用 Bash、在 Windows 可用 PowerShell。角色契约只保留 Claude Code 当前支持的 frontmatter 字段。

### 本地源码直装（备用）

先克隆仓库，再按系统执行一条：

```text
git clone https://github.com/yogogml0035-cpu/blueink-suite
cd blueink-suite
./install.sh          # macOS
.\install.ps1         # Windows PowerShell
```

两个脚本都安装到 Claude Code 的个人 skills 目录并复制完整插件。安装脚本支持 `--force` / `--dry-run`（PowerShell 为 `-Force` / `-DryRun`）。本包只面向 Claude Code。

## 第一次使用

新开 Claude Code，在**你要写稿的项目目录**里显式启动：

```
/blueink-suite 帮我为理想汽车写一篇面向行业媒体的观点供稿
```

项目未绑定时，Skill 会逐轮问出品牌和当前电脑上的知识库目录，再执行 `bind`、`index` 与 `doctor`。

Claude Code 插件形态下不要猜安装缓存地址。Skill 使用宿主提供的 `${CLAUDE_PLUGIN_ROOT}` 与 `${CLAUDE_PROJECT_DIR}`，并在任务单里写入当前机器解析出的绝对路径。macOS 通常使用 `python3`；Windows 通常优先 `py -3`，再尝试 `python`。

### 还没有知识库目录

让 `/blueink-suite` 在绑定时使用 `--create`，它会在你确认的当前电脑路径下建立：

```
理想汽车知识库/
├── README.md          这个目录放什么、不需要做什么
├── 01-原文资产/       官方资料、品牌基础、产品资料
├── 02-需求素材/       客户需求、brief、原始素材
├── 03-初终稿对比/     同一篇的初稿与终稿
├── 04-经验总结/       对接经验、传播红线、改稿规律
└── 05-成品参考/       已采用的终稿，按品类放
```

把稿件丢进去，跑一次 `index` 就能用。**三件不需要做的事**：不需要先分好类（分不清就随便放，索引会给候选标签）、不需要改文件名（文件名里的日期和品类字样是识别线索）、不需要清理旧文件（证据带时效，取证时按日期取近的）。

**只有一件事必须做：这个目录里只放这一个品牌的材料。**

然后在写稿项目目录下用显式命令启动：

```
/blueink-suite 帮我写一篇面向行业媒体的观点供稿，素材我贴在下面
```

### 这一稿的品牌和绑定的不是一个

```text
<当前平台 Python 3.9+ 命令> "<当前插件根>/scripts/blueink.py" \
  --project "<当前项目根>" check-brand --brand "东风奕派"
```

不匹配时技能不会自己选边，会把三条出路交给你：本次品牌名写错了、换到那个品牌的项目目录去、或本次只参考指定文件不用知识库。`open --brand` 会在开启运行时再挡一次，被拒绝的运行不落盘。

判定认简称——绑定「理想汽车」时说「理想」算匹配。但「现代中国」与「现代汽车」判为不匹配，因为后者下面是两个同级品牌。

### 只想参考几份指定文件

不需要知识库，也不需要绑定：

```text
<当前平台 Python 3.9+ 命令> "<当前插件根>/scripts/blueink.py" \
  --project "<当前项目根>" open --mode 生成 --brand "理想汽车" \
  --attach "<当前电脑附件绝对路径>/【指引】xxx.md" \
  --attach "<当前电脑附件绝对路径>/【新闻稿】xxx.md"
```

运行记录会把 `bound` 记为 `false`、把 `evidence_boundary` 记为 `attachments`。缺到无法成稿时技能会向你要，**不会自己去找**——没有绑定根就没有可自主检索的范围，任何目录都是越界。

### 老师带附件的任务（已绑定知识库）

老师本次明确提供的文件就是本次任务的证据，**不受绑定知识库根限制**——绑定根约束的是系统自己去找什么，不是老师授权它读什么。登记一下：

```text
<当前平台 Python 3.9+ 命令> "<当前插件根>/scripts/blueink.py" \
  --project "<当前项目根>" open --mode 生成 --brand "理想汽车" \
  --attach "<当前电脑附件绝对路径>/【指引】xxx.md" \
  --attach "<当前电脑附件绝对路径>/【新闻稿】xxx.md"
```

有附件时 `evidence_boundary` 默认为 `attachments`：**先只用附件成稿**，只有出现会阻止成稿的高影响缺口才最小范围查库，并把缺口写出来。

登记是**登记，不是审批**：它不阻止任何路径，只让"老师给的"与"自己越界读的"在证据上可区分。没登记就读，审计会判越界——这是有意的，否则 A2 只能二选一：要么放过真正的越界，要么把老师的授权判成违约。

### 双品牌客户

现代汽车在源库里是两个并列目录。**必须绑到具体品牌那一层：**

```text
# 对
--kb "<当前电脑知识库根>/现代汽车/现代N品牌"
# 错：bind 会直接拦住，不只是告警
--kb "<当前电脑知识库根>/现代汽车"
```

判定是通用结构启发式（子目录没有语料布局特征词、而是并列的若干名字），不是硬编码品牌名表——第五个品牌来了也照样生效。确认某个目录确实是单一品牌时用 `--force`，此时会明确提示跨品牌污染检查将失效。

## 目录结构

文件名一律用拉丁字母（便于 shell 调用与跨平台），**内容全部中文**。正文里用中文书名号指代同一份文件（例如《方法论内核》即 `references/methodology-core.md`），完整映射见 `SKILL.md` 的参考文件表。

```
blueink-suite/
├── SKILL.md                          方法论主入口：核心命题、五条原则、决策主干、运行底线、按需路由
├── .claude-plugin/                   Claude Code 插件与 marketplace 清单
├── agents/                           六个责任型子智能体的角色契约
│   ├── evidence-researcher.md          证据研究员
│   ├── editorial-strategist.md         编辑策略师
│   ├── professional-writer.md          专业写作者
│   ├── source-verifier.md              来源核验员
│   ├── editorial-adversary.md          编辑反方
│   └── feedback-attributor.md          反馈归因员
├── references/                       方法论展开，按需读取
│   ├── methodology-core.md             《方法论内核》为什么这套系统必须长成这样
│   ├── interview-protocol.md           《逐轮访谈协议》每轮一问、七个决策域（内部地图）、动态停止规则
│   ├── orchestration-protocol.md       《编排协议》六个角色与权限、任务单／回执契约与上限、返工判据
│   ├── evidence-tracks.md              《证据分轨取证》证据边界（附件优先）、事实／风格／编辑策略三轨
│   ├── conditional-memory.md           《条件化记忆》四级学习、置信度、反例处理
│   ├── delivery-contract.md            《交付契约》核对卡、来源清单、A/B 呈现
│   ├── category-boundaries.md          《品类任务边界》十一类交付物必须完成什么传播任务
│   ├── workspace-and-index.md          《工作空间与索引》绑定、旁路索引、官方来源白名单
│   └── troubleshooting.md              《问题定位手册》三步定位、审计结论读法、反直觉事实、留存策略
├── scripts/
│   ├── blueink.py                      唯一命令入口：确定性状态与记账层
│   ├── run_pipeline.py                 blueink.py 的别名（技能打包规范的入口约定，只转发）
│   ├── workspace.py                    单品牌工作空间绑定与路径归属
│   ├── official.py                     官方来源白名单（"什么算官方"的唯一判定点）
│   ├── index_kb.py                     旁路增量索引、Office 正文抽取、指令产物隔离
│   ├── retrieve.py                     三轨检索
│   ├── memory.py                       条件化记忆
│   ├── run_record.py                   运行记录
│   ├── audit.py                        五项验收契约的机械审计
│   ├── miniyaml.py                     YAML 子集读写（避免第三方依赖）
│   ├── validate.py                     规范自检 + 复审周期
│   ├── security_scan.py                安全扫描
│   ├── test_state.py                   状态层回归，173 项检查
│   ├── self_check.py                   自证门：版本底线 / 声明一致性 / 变异承重
│   ├── run_evals.py                    评测 harness（外来件，见下）
│   └── evolve.py                       自维护：全部质量门 + 反馈捕获（外来件）
├── assets/
│   ├── workspace.template.yaml         工作空间配置模板
│   ├── task-order-template.md          任务单模板
│   ├── decision-card-template.md       决策卡与交付模板
│   └── writing-program.schema.json     本次写作程序的 JSON Schema
├── evals/                            十三个运行记录夹具 + 十一项二元检查
├── requirements.txt                  零依赖的显式声明（不是安装清单）
├── DESIGN_NOTES.md                   设计说明：每条机械约束为什么存在、做不到什么
├── DECISIONS.md                      17 条架构决策，每条附被否掉的替代方案
├── CHANGELOG.md
├── install.sh
└── install.ps1
```

`scripts/` 里有一组**外来件**取自 `agent-skill-creator`，保持英文原样以便与上游同步：`run_evals.py`、`evolve.py`、`check_pipeline.py`。本技能自研的脚本注释与文档字符串全部为中文。

本包不需要 `staleness_check.py`、`review_staleness.py`、`schema_drift.py`、`dependency_health.py` 与 `skill_document.py`：它没有待探测的 HTTP 依赖或 API schema，复审日期由 `validate.py` 的 `review_due()` 检查。

**规模构成不靠自述，跑一条命令就能看到：**

```bash
python3 scripts/self_check.py --claims    # 末尾会打印四类构成的文件数与行数
```

分成四类看，是因为"总行数"这个数字会把性质完全不同的东西混在一起：方法论与文档（读的人是文案老师和主智能体）、自研脚本（确定性状态层，本技能自己维护）、上游同步件（可与 `agent-skill-creator` 重新同步，不由本技能演进）、审计夹具（十三种失败与合规形态的运行记录，是验证证据而不是产品代码）。同一道 `--claims` 门还会挡住脚本蔓延：**任何一个新脚本必须在上面这棵目录树或外来件清单里露面，否则直接判失败**——复杂度失控从来不是一次大改动，是一次加一个没人登记的脚本。

## 常用命令

这些是运行／排查命令，不是老师的必经操作。下文 `$BLUEINK` 代表 Claude Code 已按当前电脑解析并试跑过的完整命令：Python 3.9+、当前插件根下的 `scripts/blueink.py`、以及 `--project <当前项目根>`。它不是让老师原样输入的 shell 变量。

```bash
$BLUEINK status                   # 看当前绑定
$BLUEINK doctor                   # 一条命令看清全部状态
$BLUEINK index                    # 增量更新索引
$BLUEINK retrieve --query "交付 产能" --track fact
$BLUEINK official check-url --url "<地址>"   # 联网前必过
$BLUEINK memory list --brand "理想汽车"
$BLUEINK open --mode 生成 --attach "<老师给的附件绝对路径>"
$BLUEINK audit --run <run_id>     # 五项契约审计
$BLUEINK purge                    # 清理旧运行记录（默认试运行）
```

`--track` 取 `fact` / `style` / `strategy`。**不加 `--track` 时三轨混排，容易把初稿当风格样本，不推荐。**

两条检索时会遇到的事实：`content_status` 不是 `text` 的文件正文没被读过（DOCX/PPTX 会解包取正文，PDF、早期 Office 格式、图片只有元数据）；知识库里带 `SKILL.md` 的目录整棵子树默认不参与检索，`retrieve` 会报 `excluded_instruction_artifacts` 计数，审计这些技能包时才加 `--include-instruction-artifacts`。

## 出问题怎么查

三步：看现象落到角色 → 读该角色回执判断是"判断错了"还是"没执行" → 跑审计器。

```bash
$BLUEINK audit --run <run_id> --output "<当前电脑临时目录>/verdict.json"
```

结论只有三种：`pass`（流程没违约，**不代表稿子好**）、`violated`（看 `failed`）、`incomplete`（运行没跑完，很常见，不是 bug）。

完整对照表在 `references/troubleshooting.md`。

## 反馈怎么变成能力

系统学习的是**条件化判断**，不是不断增长的禁令。

- 老师明确说明修改原因 → 证据强度最高
- A/B 选择 → 能证明方向偏好，但不能断言原因
- 只有初稿与修改稿、没有理由 → 只记录变化，不推测动机

同一偏好在多个**独立传播事件**中重复出现则提高置信度；出现反例则**优先缩小适用范围**（`memory counter --narrow`），其次降置信度，**不删旧结论**——降置信度只是让记忆变哑，缩范围才是把观察保留下来并说清它在哪里成立。置信度上限 0.9，永不到 1.0：饱和到 1.0 会让"再来一个反例"失去意义。

声明独立事件时必须引用一次真实运行（`--run <run_id>`），不存在就拒绝。置信度完全依赖这个计数，不校验就等于允许凭空把偶发偏好推到自动生效。同一场活动内的同向修改用 `--same-event`（只 +0.05）。

记忆按品牌、触发条件、适用范围和置信度参与当前任务；`memory list` 返回当前品牌工作空间内符合查询条件的记忆。

高置信度（≥0.65）可自动进入写作程序，但必须在决策卡中可见、可取消。

`methodology` 级候选只记录，**绝不由任何工作空间自动改写通用方法论**。

## 边界

- **不能把低质量知识自动变正确。** 它让错误可定位、可归因、可迭代。头几周的产出质量取决于素材质量。
- **不保证一次就能用。** 第一质量指标是"老师把初稿改到可提交所需的修改量和修改时间明显下降"。
- **不做品类认证。** 登记在册的十一条品类（十个交付物 ＋ 活动物料这一容器类）是压力测试变量，不是验收边界。
- **能力边界是文案生成。** 不做 Word 排版、字体、配图、品牌文档模板，也不做改稿编排与版本回退。老师的修改意见只进学习外循环——要基于它再出一稿就正常发起下一次生成，不做"退到第几步"的路由。这条边界由 `self_check.py --claims` 机械守着：子命令表里出现 `rework` / `rollback` 这类名字直接判失败。
- **运行记录不永久保留。** `.blueink/runs/` 里有访谈原文与交付正文，默认保留 90 天且至少保留最近 20 次运行，用 `purge` 清理。可定位性是有留存成本的，把留存期写出来比默认永久保存诚实。
- **不承诺自然语言自动触发。** 只认 Claude Code 显式入口：默认 `/blueink-suite`，同名冲突时使用规范化入口 `/blueink-suite:blueink-suite`。

## 评测与自检

```bash
python3 scripts/test_state.py                             # 状态层回归，173 项
python3 scripts/self_check.py                             # 自证门（下面详述）
python3 scripts/check_pipeline.py .                       # 管线接线与依赖声明
python3 scripts/validate.py .                             # 规范自检
python3 scripts/security_scan.py .                        # 安全扫描
python3 scripts/run_evals.py --rollout --include-holdout   # 审计器夹具 + 基线全等比对
python3 scripts/evolve.py                                 # 一次跑完全部质量门
```

评测**不判文案好不好**（那由文案老师判断），验的是三件事：五项契约审计器自身没有静默失败（13 个夹具刻画 13 种形态，含 2 个保留测试）；确定性状态层还守着它声称的边界（绑定拦截、指令产物隔离、URL 白名单、置信度规则）；**技能对外的声明与实际一致**。

普通运行和当前发布门不调用模型 API。上游 `run_evals.py` 保留了可选 `--judge` 能力：只有显式使用且环境中存在 `ANTHROPIC_API_KEY` 时，才会访问 Anthropic Messages API；当前评测规格没有 `llm-judge` 判据，因此这条地址不在默认执行路径。

### 自证门：把声明变成机械检查

前两件事守的是"代码是否还对"。第三件守的是另一类失败——它不会报错，只会让人在某一天发现"文档说的和实际做的不是一回事"。

```bash
python3 scripts/self_check.py --compat     # 版本底线：声明 3.9 就必须真的能在 3.9 跑
python3 scripts/self_check.py --claims     # 声明一致性：数字、子命令面、边界、脚本清单双向核对
python3 scripts/self_check.py --mutation   # 变异承重：真的注入七个已知失败形态
```

`--mutation` 是这道门里最不能省的一段。"我们做过变异测试"写在 README 里只是一句话——**一段声称能抓住错误的检查，在它从没被喂过错误输入的情况下，和一段 `return True` 无法区分。** 所以它每次都真的往技能副本里注错，看检查会不会红：

| 变异 | 注入的失败形态 | 必须转红 |
|---|---|---|
| `index-hash` | 增量索引改用 size+mtime 判断复用 | `test_state.py` |
| `instruction-artifact` | 历史技能包只隔离入口文件，不隔离整棵子树 | `test_state.py` |
| `confidence-cap` | 置信度上限放宽到 1.0 | `test_state.py` |
| `url-whitelist` | 官方来源白名单退化成子串匹配 | `test_state.py` |
| `audit-incomplete` | 审计器把「运行没跑完」判成「流程通过」 | `run_evals.py --rollout --include-holdout` |
| `sufficiency-dimensions` | 访谈停止理由不再要求指名会改变稿件的维度 | `test_state.py` |
| `duplicate-question` | 同一个问题重复追问却不再判违约 | `test_state.py` |

前两条锁的是这个技能最实质的两条优势，而它们退化时**症状都是静默的**：按 size+mtime 判断复用的索引，在文件被同长度改写并恢复修改时间后会漏更新——文件确实变了，检索却永远拿不到新内容，且不报任何错；只隔离入口文件的做法会让技能包 `references/` 里的固定模板重新进入检索，于是"新闻稿必须先铺垫行业背景"这类固定模板接管本次判断。这两条只能靠变异门守。

变异靶点找不到时判**失败**而不是跳过：一个打不进去的变异什么都没测到，却会让这道门继续显示绿色。

两条纪律：每项契约检查都必须有对应夹具（否则零回归可能只是盲区）；夹具之间的契约必须相互独立（一项违约不能连带另一项）。

**明确没有被自动证明的：** 没有跑过真实的主／子智能体端到端文案任务。审计器守的是形式契约，"六个角色协作出来的稿子是否真的更好"必须安装后由文案老师盲评。完整清单见 `DESIGN_NOTES.md` 最后一节。

## 维护

以下命令从仓库根运行；macOS 使用 `python3`，Windows PowerShell 将命令开头替换为已验证的 `py -3` 或 `python`。

```bash
python3 scripts/evolve.py                          # 全部质量门：状态层 / 自证 / 管线 / 规范 / 安全 / 评测
python3 scripts/evolve.py --correct "<它哪里错了>"   # 把一次纠正写进 Gotchas
```

`--correct` 捕获的是任何自动检查都推不出来的东西：真实使用者看到错误输出的那一刻。这是 `## Gotchas` 累积真知识的入口。

## 许可

内部使用（proprietary）。新蓝标数字 · 汽车事业群 · AI 内容中台。
