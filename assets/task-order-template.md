# 任务单模板

主智能体启动子智能体时用的 prompt 结构。第一行固定为读取角色契约的指令——**这行不能省**，它是子智能体在任何安装形态下都能拿到角色定义的保证。

角色名与契约文件同名，一一对应：

| 中文角色 | `role` ／ 文件名 |
|---|---|
| 证据研究员 | `evidence-researcher` |
| 编辑策略师 | `editorial-strategist` |
| 专业写作者 | `professional-writer` |
| 来源核验员 | `source-verifier` |
| 编辑反方 | `editorial-adversary` |
| 反馈归因员 | `feedback-attributor` |

```text
先读取并严格遵守 {skill_root}/agents/{role}.md，把它当作你的完整角色定义。
读完后执行下面的任务单，不要执行任务单之外的任何动作。

【任务单】
run_id: {run_id}
task_id: {T1..T6}
role: {evidence-researcher | editorial-strategist | professional-writer |
       source-verifier | editorial-adversary | feedback-attributor}
instance: {a | b}                       # 仅策略师与写作者的双实例需要
brand: {品牌名}
kb_root: {绑定的知识库绝对路径}
skill_root: {技能根目录绝对路径}
project_root: {当前项目根绝对路径}
python: {当前平台已验证可用的 Python 3.9+ 命令：macOS 通常是 python3；Windows 通常是 py -3}
cli: {用当前 shell 正确引用 python、skill_root 与 project_root 后得到的可直接执行命令}
evidence_boundary: {attachments | kb}    # 取证角色必给
attachments:                             # 老师本次显式提供的文件，已登记
  - {绝对路径}
ask: |
  {本次要解决什么。一到三句，写清任务而不是写清流程，不写答案}
context:
  已确认: {访谈里已经定下来的判断，只给这个角色用得上的}
  待定: {尚未解决、但这个角色需要知道的}
inputs:                                  # 上游回执的绝对路径，由主智能体显式传
  - {…/runs/<run_id>/evidence.json}
constraints:
  - {硬约束，每条都要是可判断的}
expect: {…/runs/<run_id>/<回执文件>.json}
```

## 五条必须遵守的传递纪律

1. **`skill_root`、`project_root`、`kb_root`、`cli`、`expect` 都按当前电脑生成。** 路径必须是当前平台的绝对路径；`cli` 必须已用当前 shell 试跑过，不能把另一台电脑的路径或 `python3` 假定复制过来。子智能体的工作目录不保证与主智能体一致。
2. **整份任务单不超过 20 行，`ask` 不超过 3 行。** 超出的部分几乎总是主智能体把自己的结论提前写了进去。
3. **不预置结论。** `ask` 只说要解决什么问题。给编辑策略师的任务单里出现"建议以战略定力为主线"这类句子，独立判断就已经结束了——它会去论证你的结论，而你需要的是一个有可能反对你的判断。
4. **`context` 只给最小集。** 不要把整轮访谈记录倒给子智能体。
5. **`inputs` 里绝不给 `strategy.json` 的理由给来源核验员和编辑反方。** 理由会说服它们，而它们的价值恰在不被说服。

## 各角色的输入白名单

以 `references/orchestration-protocol.md` 第二节为准，不在这里复制一份——两处清单一旦发散，就会有角色拿到它不该看的上下文。

## 一个填好的例子

```text
先读取并严格遵守 <当前技能根绝对路径>/agents/editorial-strategist.md，
把它当作你的完整角色定义。读完后执行下面的任务单，不要执行任务单之外的任何动作。

【任务单】
run_id: 20260822-143512-lixiang
task_id: T2
role: editorial-strategist
instance: a
brand: 理想汽车
kb_root: <当前电脑上的理想汽车知识库绝对路径>
skill_root: <Claude Code 当前插件根绝对路径>
project_root: <当前项目根绝对路径>
python: <当前平台已验证可用的 Python 3.9+ 命令>
cli: <当前 shell 可直接执行的 blueink.py 绝对命令，显式带 --project>
ask: |
  为「i8 上市后首月交付」写一篇面向行业媒体的观点供稿。
  判断本稿必须完成什么传播任务，给出推荐主线、素材取舍与信息预算。
context:
  已确认: 读者为行业媒体记者；期望效果是让记者理解平台能力而非单看销量
  待定: 良品率具体数值能否披露，老师尚未答复
inputs:
  - <当前项目根绝对路径>/.blueink/runs/20260822-143512-lixiang/evidence.json
constraints:
  - 不补 evidence.json 里没有的事实
  - 不产出完整正文
  - 只在两条主线都被同一批事实支撑时才报备选
expect: <当前项目根绝对路径>/.blueink/runs/20260822-143512-lixiang/strategy.json
```

注意这个例子里 `ask` **没有**说"建议以平台能力为主线"。它只给了任务和读者，主线是策略师要自己判断的东西——那正是启动这个角色的全部理由。
