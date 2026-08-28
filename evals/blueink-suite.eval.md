# blueink-suite 机械合同回归

这份评测只验证 BlueInk 的确定性状态层和六项审计合同，不评文案质量、不评模型表达，也不模拟真实 Claude Code 对话。

## 被测对象

```bash
python3 scripts/blueink.py audit --input <运行记录目录> --output <结论.json>
```

`run` 命令对每个 golden 运行同一个审计器。golden 是人工确认过的运行记录夹具；`expected.json` 是审计器应该给出的独立结论。基线比较验证 A1—A6 的状态、失败项、定位证据和缺失产物，而不是验证某一段文案。

## 六项机械合同

| id | 合同 | 通过边界 |
|---|---|---|
| A1 | 入口唯一 | `run.json` 使用受支持的 `/blueink-suite` 入口，运行标识和 schema 合法 |
| A2 | 单品牌隔离 | 事实、风格参考和交付来源都在登记附件或绑定知识库范围内 |
| A3 | 动态访谈 | Schema 6 生成任务至少一轮、一次只问一个问题，最后一轮确认方向且得到老师确认 |
| A4 | 阶段边界 | 当前运行只使用 Schema 6 产物，不生成 `draft.md` 等旧阶段文件，核验角色和方向记录合法 |
| A5 | 输出有效 | `delivery.md`、`verify.json`、`delivery-check.md` 的结论和来源结构一致；未归档缺侧车只能判 `incomplete` |
| A6 | 通用规范 | `run.json` 记录规范版本、全部硬规则、正文哈希和命中状态；带命中项或失效回执的正文不能交付 |

`schema5` 与 `schema4` 夹具只验证对应运行记录可被审计。新运行只能写入 schema 6，唯一正文是 `delivery.md`，交付核对信息单独位于 `delivery-check.md`。

## Golden 用例

| 用例 | 目的 | 应判 | split |
|---|---|---|---|
| `schema6-pass-common-policy` | 中性呈现合作品牌，通用规范逐条检查且绑定正文 | `pass` | val |
| `schema6-violation-common-policy` | 带竞品攻击命中项的正文绕过交付门 | `violated` A6 | val |
| `schema5-pass-attachment` | 附件快线：方向已确认，唯一正文、核验和核对侧车齐全 | `pass` | val |
| `schema5-pass-kb` | 绑定知识库：事实来源在品牌根内，强比较词有授权 | `pass` | val |
| `schema5-violation-isolation` | 交付来源引用未登记的其他品牌附件 | `violated` A2 | val |
| `schema5-violation-interview` | Schema 5 生成任务零轮访谈 | `violated` A3 | val |
| `schema5-violation-stage` | Schema 5 运行目录混入 `draft.md` | `violated` A4 | val |
| `schema5-incomplete` | 尚未归档，缺 `delivery-check.md` | `incomplete`，不是违约 | **test** |
| `schema5-violation-output` | `verify.json` 的结论与实际 claims 不一致 | `violated` A5 | val |
| `legacy-schema4-readonly` | Schema 4 历史运行按旧产物合同继续可审计 | `pass` | val |

`schema5-incomplete` 是保留测试：它防止审计器把运行中的缺产物误判为违规。它只在发布前通过 `--include-holdout` 执行，不进入任何优化循环。

## 评测层次

评测规格内的 5 个 command criteria 只检查审计结论自身的 schema、自洽性、可定位性、解释完整性和六项合同名称。`--rollout` 另外把实际审计输出与每个 golden 的 `expected.json` 做 JSON 值比较，因此 A1—A6 的具体判定也会回归。

当前规模为 10 个夹具 × 5 项命令检查，共 50 项；其中日常评分跳过 1 个 holdout，发布前再完整执行。

状态层和自证门是独立发布门，不对每个 golden 重复执行：

```bash
python3 scripts/test_state.py                       # 当前状态层集成回归，275 项
python3 scripts/self_check.py --compat --claims     # 版本、声明、夹具统计和边界一致性
python3 scripts/self_check.py --mutation            # 已知失败形态必须转红
python3 scripts/check_pipeline.py .                 # 脚本接线与依赖
python3 scripts/validate.py .                       # Skill 特有合同
python3 scripts/security_scan.py .                  # 安全边界
```

## 怎么跑

```bash
python3 scripts/run_evals.py --validate
python3 scripts/run_evals.py
python3 scripts/run_evals.py --rollout
python3 scripts/run_evals.py --rollout --include-holdout
```

`--rollout` 重新执行审计器并比较 JSON 基线。详情文字是定位证据的一部分；改变它必须同步确认对应 golden。这个评测不会启动 Claude、不会调用模型，也不会把结构通过写成业务质量通过。

```json
{
  "skill": "blueink-suite",
  "run": "python3 scripts/blueink.py audit --input {input} --output {output} --exit-zero",
  "criteria": [
    {"id": "schema", "text": "审计结论结构完整，六项契约一条不少", "type": "command", "cmd": "python3 scripts/blueink.py check-verdict {output} --check schema"},
    {"id": "consistent", "text": "verdict 与 failed、missing_artifacts 自洽", "type": "command", "cmd": "python3 scripts/blueink.py check-verdict {output} --check consistent"},
    {"id": "localisable", "text": "每条违约都能定位到具体文件或字段", "type": "command", "cmd": "python3 scripts/blueink.py check-verdict {output} --check localisable"},
    {"id": "explained", "text": "每条检查都有说明，跳过的不伪装成通过", "type": "command", "cmd": "python3 scripts/blueink.py check-verdict {output} --check explained"},
    {"id": "contracts", "text": "六项合同名称未被改名或替换", "type": "command", "cmd": "python3 scripts/blueink.py check-verdict {output} --check contracts"}
  ],
  "golden": [
    {"id": "schema6-pass-common-policy", "input": "golden/schema6-pass-common-policy/run", "expected": "golden/schema6-pass-common-policy/expected.json", "split": "val"},
    {"id": "schema6-violation-common-policy", "input": "golden/schema6-violation-common-policy/run", "expected": "golden/schema6-violation-common-policy/expected.json", "split": "val"},
    {"id": "schema5-pass-attachment", "input": "golden/schema5-pass-attachment/run", "expected": "golden/schema5-pass-attachment/expected.json", "split": "val"},
    {"id": "schema5-pass-kb", "input": "golden/schema5-pass-kb/run", "expected": "golden/schema5-pass-kb/expected.json", "split": "val"},
    {"id": "schema5-violation-isolation", "input": "golden/schema5-violation-isolation/run", "expected": "golden/schema5-violation-isolation/expected.json", "split": "val"},
    {"id": "schema5-violation-interview", "input": "golden/schema5-violation-interview/run", "expected": "golden/schema5-violation-interview/expected.json", "split": "val"},
    {"id": "schema5-violation-stage", "input": "golden/schema5-violation-stage/run", "expected": "golden/schema5-violation-stage/expected.json", "split": "val"},
    {"id": "schema5-incomplete", "input": "golden/schema5-incomplete/run", "expected": "golden/schema5-incomplete/expected.json", "split": "test"},
    {"id": "schema5-violation-output", "input": "golden/schema5-violation-output/run", "expected": "golden/schema5-violation-output/expected.json", "split": "val"},
    {"id": "legacy-schema4-readonly", "input": "golden/legacy-schema4-readonly/run", "expected": "golden/legacy-schema4-readonly/expected.json", "split": "val"}
  ]
}
```

## 未覆盖边界

- 不证明 Claude Code 是否真的按访谈指导行动；这里只审计已经落盘的运行记录。
- 不证明文案质量、事实判断正确性、风格满意度或老师修改量。
- 不把同一上下文内的来源核验称为独立评审。
- 不把 `pass` 解读成可以直接提交，只表示机械合同没有发现违约。
