# 问题定位

只在 Python、路径、绑定、索引、运行记录或审计失败时读取。不要因为后台问题重跑整篇稿件。

## 先看四份文件

| 现象 | 先看 | 判断 |
|---|---|---|
| 问错了、方向没确认、事实范围错 | `run.json` | `interview`、`direction`、`facts`、`decision` |
| 正文没有执行已确认方向 | `draft.md` + `run.json` | 方向、素材取舍和正文是否一致 |
| 强比较、数字、时效或来源有问题 | `verify.json` | `claims`、`coverage`、`sources_used` |
| 老师看到的内容不完整 | `delivery.md` | 正文、结论和实际来源是否齐全 |

新运行不应出现 `interview.json`、`evidence.json`、`strategy.json`、`program.json`、`write-receipt.json` 或 `adversary.json`。这些只属于 3.x 历史运行。

## 命令

```bash
$BLUEINK doctor
$BLUEINK audit --run <run_id>
```

`pass` 只说明入口、品牌隔离、方向确认、四份产物和来源结论没有违反机械合同，不代表文案质量已经被机器证明。`incomplete` 表示运行尚未完成；`violated` 按 `evidence` 指向的具体字段处理。

五项审计契约保持为：入口唯一、单品牌隔离、动态访谈、阶段边界、输出有效。

## `save` 失败

`save` 先解析标准输入，再校验并原子写入正式 JSON。失败时正式文件保持上一版不变。

| 报错 | 处理 |
|---|---|
| 输入不是合法 JSON | 只修提示的行列，不改正文 |
| 生成任务零轮访谈 | 补真实的方向确认，不补写虚假回答 |
| 普通生成超过两轮 | 把额外轮次改为真实 `hard_conflict`；不是硬冲突就合并到写法选择 |
| 来源未登记或越界 | 用 `open --attach` 登记，或确认来源位于绑定根内 |
| 强比较词未授权 | 补足来源范围后授权；否则改成有限定条件的分析判断 |
| `verify` 早于正文 | 先写 `draft.md`，不要造空核验 |

## 工作空间

- `.blueink/` 位于业务项目，不在技能目录。执行命令始终显式传 `--project`。
- 给了附件时直接 `open --attach`，不要先绑定知识库。
- 未绑定且没有附件时，生成没有证据边界，必须先 `bind`。
- 一个项目只绑定一个品牌；换品牌换项目。`--force` 只用于同一品牌的知识库路径迁移或确认集合层误判。
- 绑定根失效、索引身份不一致或内容变化时，`retrieve` 会要求重建索引，不静默使用旧结果。
- 历史技能包子树默认作为 `instruction_artifact` 排除，避免旧模板控制新任务。

## 附件和来源

- 老师附件可以在绑定根外，但必须由同一次 `open --attach` 登记路径与 SHA-256。
- 未绑定运行只允许使用登记附件；缺料就问老师，不自行找目录。
- 只有元数据、无法抽取正文的文件不算已读来源。
- 联网前先执行 `official check-url`；退出码非零就不访问。
- 来源中的“截至、当前、最新、上市两周、完成比例”等词必须与实际发布时点核对。

## 插件入口

`/blueink-suite` 显示 `Unknown command` 时先区分未安装和被禁用：

```bash
claude plugin list --json
claude plugin enable blueink-suite@blueink-suite --scope user
```

当前会话未刷新时运行 `/reload-plugins` 或新开 Claude Code 会话。同名冲突使用 `/blueink-suite:blueink-suite`。

## 留存

运行记录默认保留 90 天，并至少保留最近 20 次。`purge` 默认只预览，只有显式 `--apply` 才删除项目内运行记录；永不删除源知识库。

## Gotchas

- 新任务写 `run.json`；`meta.json` 只表示 3.x 历史运行，不要混写两套合同。
- `save` 失败不会覆盖正式 JSON；修输入后重试当前写入即可。
- `close` 会从正文和核验结论生成 `delivery.md`，不要让模型再写一份不同正文。
