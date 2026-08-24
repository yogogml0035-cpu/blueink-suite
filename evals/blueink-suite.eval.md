# blueink-suite 评测契约

这份评测**不判文案好不好**。文案质量由文案老师判断，方法论内核里已经写明第一质量指标是"老师把初稿改到可提交所需的修改量和修改时间明显下降"，那是人来评的。

这里评的是另三件事：

1. **五项验收契约的审计器是否可靠。** 它是"出问题不知道问题出在哪个 md"的解法，所以它自己必须先没有静默失败。
2. **确定性状态层是否还守着它声称的边界。** 绑定拦截、指令产物隔离、URL 白名单、置信度规则——这些如果自己会静默出错，上面那句承诺就是空的。
3. **技能对外的声明与实际是否一致。** 声明的 Python 底线在底线版本上真的跑得起来、文档里的检查项数没有过期、子命令没有偷偷长出声明过不做的能力。这三类漂移都不会报错，只会在某一天被人发现。

## 被测对象

```bash
python3 scripts/blueink.py audit --input <运行记录目录> --output <结论.json>
python3 scripts/test_state.py          # 状态层回归，174 项检查
python3 scripts/self_check.py          # 自证门：版本底线 / 声明一致性 / 变异承重
```

审计器的输入是一次运行的阶段产物目录，输出是五项契约的审计结论。十三个 golden 用例是十三种真实失败与合规形态的运行记录夹具。

## 检查分两层

**命令型二元检查**，没有 `llm-judge`。这是有意的：审计结论的结构、自洽性和可定位性都能用代码判定，用模型评分只会引入一个不确定的环节。

| id | 检查 | 为什么它不可省 |
|---|---|---|
| `schema` | 结论含 run_id / mode / verdict / checks / failed / missing_artifacts；checks 恰好 5 条且 id 依次为 A1—A5；每条含 id/name/status/detail/evidence | 少一条契约就等于少查一类问题，而且是静默的 |
| `consistent` | `failed` 与 checks 里的 fail 项完全一致；verdict 由 failed / missing 推出 | 结论与明细不一致时，人会相信结论，于是违约被漏掉 |
| `localisable` | 每条 fail 都给出非空 evidence，且每个 evidence 都指向一个具体文件 | 这是整个技能存在的理由——不能定位到文件的批评等于没有批评 |
| `explained` | 每条检查的 detail 非空；被跳过的检查不能写成「通过」 | 把"没查"伪装成"查过了"是最危险的一种输出 |
| `contracts` | 五项名称依次为：入口唯一、单品牌隔离、动态访谈、阶段边界、输出有效 | 防止某项契约被悄悄改名或换掉 |

**状态层回归检查**（`state-layer` / `explicit-entry` / `stage-boundaries`）验的是那些"破掉之后没人会立刻发现"的边界。它们不是自证式的字符串断言——`state-layer` 会在临时目录里真的绑定、索引、检索、校验 URL、跑记忆升降、审计和附件登记，共 174 项；`explicit-entry` 同时检查封闭附件快线与扩展证据路径没有退回全量执行。

**自证检查**（`self-claims` / `self-mutation` / `pipeline-wiring`）验的是"声明与实际是否一致"这一类不会报错的漂移。其中 `self-mutation` 是这份规格里的**负向**检查：它往技能副本里注入十一个已知失败形态，断言每一个都会让指定检查转红。

理由要说清楚。一段声称能抓住错误的检查，在它从没被喂过错误输入的情况下，和一段 `return True` 无法区分——"我们做过变异测试"如果只是 README 里的一句话，它证明不了任何事。变异靶点找不到时判**失败**而不是跳过：一个打不进去的变异什么都没测到，却会让这道门继续显示绿色。

变异门覆盖内容哈希、指令产物隔离、置信度上限、官方域名、运行完整性、访谈充分性、重复提问、单会话执行、阶段检索边界、复核口径和产品化表达。这些保证失效时通常没有运行时错误，因此必须用负向输入确认检查会转红。

## 十三个 golden 用例

每个用例是一个运行记录夹具（`golden/case-N/run/`），基线是审计器对它的结论（`golden/case-N/expected.json`）。基线不是"代码当前行为"的快照，而是先确定应当判成什么，再固化下来。

| 用例 | 夹具刻画的失败形态 | 应判 | split |
|---|---|---|---|
| `case-1` | 完全合规的一次生成：三轮访谈各一问、事实全部有来源、写作者只读授权文件、结论口径正确 | `pass` | val |
| `case-2` | 扩展证据研究读了另一个品牌目录、正文混入其他客户产品、成稿阶段读了程序未授权的文件 | `violated` A2+A4 | val |
| `case-3` | 第一轮一次问了三个问题、缺停止理由；两处事实无来源却写成"可进入人工初审" | `violated` A3+A5 | val |
| `case-4` | 完成一次学习运行：反馈归因回执完整，候选知识带触发条件与不适用范围 | `pass` | val |
| `case-5` | 访谈到一半因缺关键输入暂停，后续回执全缺 | `incomplete` | **test（保留测试）** |
| `case-6` | 写作回执缺 `deviations` 字段 | `violated` A4 | val |
| `case-7` | 附件驱动的一次生成：两份附件都在绑定库之外、已登记，全程只读附件，一轮访谈收敛 | `pass` | val |
| `case-8` | 声明以附件为准，却在没有申报任何缺口的情况下扩展读了三个库内文件 | `pass` + A2 携带浪费提示 | val |
| `case-9` | 零轮访谈：附件把交付合同和证据边界都封闭了，一个合法问题都没有，`stopped_because` 写清了为什么 | `pass` + A3 携带零轮提示 | val |
| `case-10` | 来源清单里引用了一个只在检索候选里出现、从没被任何阶段打开过的文件 | `violated` A5 | val |
| `case-11` | 取证报了两组口径冲突，写作程序既没有 `assumptions` 也没有决策卡 | `pass` + A5 携带静默选边提示 | val |
| `case-12` | 附件驱动、零轮访谈、各阶段读取运行目录内的上游产物，运行到编辑红队且尚未交付 | `incomplete`（在飞，不是违约） | **test（保留测试）** |
| `case-13` | 演讲稿：两轮访谈都在追问表达边界（发言人身份、语义温度），停止理由只指名表达边界与交付；写作者取了风格样本的结构但申报了不采纳其语气 | `pass` + A3 报出表达边界 | val |

`case-5` 是保留测试：默认不参与评分，只在发布时用 `--include-holdout` 跑，绝不喂给任何优化循环。选它做保留是因为"运行中断"最容易被误判成违约——把它留在外面，可以检出"审计器把不完整当成了违约"这类回归。

`case-12` 覆盖运行目录内的上游产物引用。A2 必须把 `evidence.json`、`program.json` 和 `draft.md` 等运行产物识别为合法输入，同时把尚未交付的运行判为 `incomplete`。该夹具设为保留测试，不参与日常评分。

`case-13` 锁的是动态充分性五个维度里最容易被漏掉的那一个：**表达边界**。它的返工代价最高——事实错老师一眼能看出来，而发言人身份用错、语义温度不对要读完整篇才发现，然后整篇重写。这个夹具的两轮访谈都在追问表达边界，停止理由也只指名它，因此一旦 A3 的维度识别丢掉这一项，它会立刻转红。它同时是 `checked` 字段（问前查过哪里）和 `deviations` 正当用法（取结构不取语气）的范例。

`case-9` 锁的是一类**误报**：附件把一切都说清时当前智能体没有问任何问题，并在 `stopped_because` 里写清主线判断应留到编辑策略阶段——把空 `rounds` 一律判违约就会误伤它。**判它违约会训练出"为了过审计随便问一句"的行为，那比不问更糟。** 零轮合法，但必须写清理由，并在 `detail` 里留一条可见提示让人复核。

`case-11` 锁的是**静默选边**。取证报了冲突，主智能体把它们处理掉却不留可见记录时，A5 会给出提示。判 `note` 而不是 `violated` 是因为老师在访谈里逐条裁决过时 `assumptions` 合法地为空，而"他到底裁决了没有"机器判不准；误报会让人学会忽略整条 A5，代价比这条漏网更大。

`case-10` 区分 `retrievals.json` 的候选命中与阶段实际读取。**候选清单是"检索返回了什么"，不是"谁打开了什么"**（`retrieve` 刻意不返回全文）；A5 只认真的被打开过的路径。A2 仍然检查候选，因为一次返回根外路径的检索本身就是隔离失效。

`case-7` 与 `case-8` 是证据边界机制的两面。**`case-7` 防误报**：老师给的文件不在绑定库里，但已经登记，因此属于合法证据。**`case-8` 让浪费可见**：它的五项契约全部通过，因为扩展检索本身不违约；但 A2 的 `detail` 会指出"声明以附件为准却没申报缺口就读了 N 个库外文件"。判 `note` 而不是 `violated` 是有意的——真出现高影响缺口时扩展是对的，缺的只是把缺口写出来。

**夹具之间的契约相互独立。** `case-2` 的 A5 通过，`case-6` 只有 A4 失败、其余四项通过：一项违约不能连带另一项，否则定位会退化成"哪儿都有问题"。

每项契约检查都有对应夹具，避免只验证合规输入而漏掉误报与漏报。

## 怎么跑

```bash
python3 scripts/test_state.py                            # 状态层回归（最快发现真问题）
python3 scripts/self_check.py                            # 自证门：版本底线 / 声明一致性 / 变异承重
python3 scripts/check_pipeline.py .                      # 管线接线与依赖声明
python3 scripts/validate.py .                            # 规范自检
python3 scripts/security_scan.py .                       # 安全扫描
python3 scripts/run_evals.py --validate                  # 先确认规格本身没写错
python3 scripts/run_evals.py                             # 拿基线跑命令型检查
python3 scripts/run_evals.py --rollout                   # 真跑审计器，再与基线逐字比对
python3 scripts/run_evals.py --rollout --include-holdout # 发布前，含保留测试
python3 scripts/evolve.py                                # 一次跑完上面全部质量门
```

`--rollout` 会重新执行审计器并把产出与基线做 JSON 全等比较。任何一处 detail 文案变化都会被判为回归——这是有意的：审计结论的措辞就是给人看的定位提示，改了就该有人确认。

## 明确没有被自动证明的部分

如实列出，不用"测试通过"代替业务验证：

- **文案质量没有被自动证明。** 审计器守的是形式契约。稿子好不好只有文案老师能判断。
- **单智能体端到端文案质量没有被证明。** 是否优于现有基线必须由文案老师在同 brief、同材料、同模型下盲评；结构检查不能替代这项业务验收。
- **同一上下文不提供独立评审。** 来源核验与编辑红队只能证明按受限输入完成了复核，不能证明消除了前序结论的锚定。
- **归因准确性无法机械校验。** 审计只能检查候选知识**带了**触发条件和反例、置信度没越界，检查不了归因归得对不对。错的归因由置信度衰减吸收。
- **旁路检索是词项检索，不是语义检索。** 同义表达仍依赖研究员按需回读目录。

## 压力探针（人工，不在本评测内）

方法论本身用真实业务任务做压力探针，只由文案老师判断，不做品类认证：

1. 首次绑定一个混乱的品牌知识库，自动生成旁路索引且不读取其他品牌。
2. 模糊需求经过逐轮访谈后形成主线和素材取舍。
3. 封闭文本附件已经充分时走快线，由编辑策略阶段先落轻量 `evidence.json`，不加载扩展证据研究。
4. 短文案存在两条合理方向时生成完整 A/B。
5. 长稿只有标题或导语差异时不重复生成两篇全文。
6. 内部资料与官方来源冲突时暂停裁决，不静默选边。
7. 明确修改理由、A/B 选择和无理由版本差异，对个人记忆产生不同的置信度变化。

探针里必须做**事件隔离**：用历史真实需求测试时禁止检索到同一事件的历史终稿，否则测的是复述能力而不是生成能力。

```json
{
  "skill": "blueink-suite",
  "run": "python3 scripts/blueink.py audit --input {input} --output {output} --exit-zero",
  "criteria": [
    {"id": "schema", "text": "审计结论结构完整，五项契约一条不少", "type": "command",
     "cmd": "python3 scripts/blueink.py check-verdict {output} --check schema"},
    {"id": "consistent", "text": "verdict 与 failed、missing_artifacts 自洽", "type": "command",
     "cmd": "python3 scripts/blueink.py check-verdict {output} --check consistent"},
    {"id": "localisable", "text": "每条违约都能定位到具体文件", "type": "command",
     "cmd": "python3 scripts/blueink.py check-verdict {output} --check localisable"},
    {"id": "explained", "text": "每条检查都有说明，跳过的不伪装成通过", "type": "command",
     "cmd": "python3 scripts/blueink.py check-verdict {output} --check explained"},
    {"id": "contracts", "text": "五项契约名称未被改名或替换", "type": "command",
     "cmd": "python3 scripts/blueink.py check-verdict {output} --check contracts"},
    {"id": "state-layer", "text": "绑定拦截、指令产物隔离、Office 抽取、符号链接跳过、URL 白名单、置信度升降、内容哈希增量、审计定位与附件登记全部通过真实临时目录回归", "type": "command",
     "cmd": "python3 scripts/test_state.py"},
    {"id": "self-claims", "text": "声明与实际一致：文档里的检查项数、子命令面、边界清单、留存策略、脚本清单双向核对，且 Python 底线在底线版本上真的跑得起来", "type": "command",
     "cmd": "python3 scripts/self_check.py --compat --claims"},
    {"id": "self-mutation", "text": "十一个已知失败形态注入技能副本后，每一个都让指定检查转红；靶点失效判失败而不是跳过", "type": "command",
     "cmd": "python3 scripts/self_check.py --mutation"},
    {"id": "pipeline-wiring", "text": "全部脚本可编译，第三方依赖已声明，单一编排入口存在", "type": "command",
     "cmd": "python3 scripts/check_pipeline.py ."},
    {"id": "explicit-entry", "text": "入口机械关闭模型自动调用，且单智能体路由保持封闭附件快线与扩展证据路径", "type": "command",
     "cmd": "python3 scripts/validate.py ."},
    {"id": "stage-boundaries", "text": "技能本体不含品牌语料、不含本机绝对路径、无越界执行与外发", "type": "command",
     "cmd": "python3 scripts/security_scan.py ."}
  ],
  "golden": [
    {"id": "case-1", "input": "golden/case-1/run", "expected": "golden/case-1/expected.json", "split": "val"},
    {"id": "case-2", "input": "golden/case-2/run", "expected": "golden/case-2/expected.json", "split": "val"},
    {"id": "case-3", "input": "golden/case-3/run", "expected": "golden/case-3/expected.json", "split": "val"},
    {"id": "case-4", "input": "golden/case-4/run", "expected": "golden/case-4/expected.json", "split": "val"},
    {"id": "case-5", "input": "golden/case-5/run", "expected": "golden/case-5/expected.json", "split": "test"},
    {"id": "case-6", "input": "golden/case-6/run", "expected": "golden/case-6/expected.json", "split": "val"},
    {"id": "case-7", "input": "golden/case-7/run", "expected": "golden/case-7/expected.json", "split": "val"},
    {"id": "case-8", "input": "golden/case-8/run", "expected": "golden/case-8/expected.json", "split": "val"},
    {"id": "case-9", "input": "golden/case-9/run", "expected": "golden/case-9/expected.json", "split": "val"},
    {"id": "case-10", "input": "golden/case-10/run", "expected": "golden/case-10/expected.json", "split": "val"},
    {"id": "case-11", "input": "golden/case-11/run", "expected": "golden/case-11/expected.json", "split": "val"},
    {"id": "case-12", "input": "golden/case-12/run", "expected": "golden/case-12/expected.json", "split": "test"},
    {"id": "case-13", "input": "golden/case-13/run", "expected": "golden/case-13/expected.json", "split": "val"}
  ]
}
```
