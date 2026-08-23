---
description: 显式启动蓝墨 BlueInk 证据驱动的汽车公关文案编辑决策系统（单品牌单老师工作空间）。
disable-model-invocation: true
---

这是 `/blueink-suite` 的唯一入口。

读取 `${CLAUDE_PLUGIN_ROOT}/SKILL.md`，它是方法论主入口：一个核心命题、五条方法论原则、有向决策主干和五条运行底线。**其余内容按需读取**——`SKILL.md` 末尾的路由表说明什么时候读哪一份 reference，不要一次全读。

第一句回复必须是启动回执行，不加寒暄：

```
BlueInk 已启动 · run-id: <run_id> · 品牌: <brand> · 老师: <teacher> · 模式: <生成 | 绑定 | 学习 | 定位>
```

老师本次带了附件时，先用 `blueink.py open --attach <绝对路径>` 登记，再按"以附件为准"的封闭证据边界工作。

不要因普通自然语言自动执行，不要响应旧入口 `/blueink`，不要在没有申报缺口的情况下把附件驱动的任务扩展成整库检索，不要把本命令扩展成 Word 排版或完整改稿系统。
