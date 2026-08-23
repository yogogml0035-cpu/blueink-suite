#!/usr/bin/env python3
"""规范自检：这个技能包自己是否还满足它声称的那些契约。

官方 ``agent-skill-creator`` 的 validate 检查的是通用技能规范（frontmatter 齐不齐、
目录名对不对）。这里检查的是**本技能特有的、一旦破掉就会静默失效的东西**：

- 入口是否真的机械关闭了模型自动调用（散文声明不算）；
- 六个角色契约是否都在，写作者是否真的没有检索工具；
- 文档里引用的文件是否真的存在（断链的"按需读取"等于没有那一节）；
- 技能本体是否混进了品牌语料或绝对路径（技能必须能整目录复制给另一位老师）；
- 六项验收契约的名称有没有被悄悄改掉；
- 复审日期是否已经越过声明的复审周期；
- 任务单的必填字段在《编排协议》与 assets 模板两处是否都还在（发散时后写的那份会静默失效）；
- `evolve --correct` 的落点标题是否还在，以及它有没有跑回 `SKILL.md`（首读层是方法论）；
- `blueink.py` 的每个参数是否在文档里出现过（**没被任何文档提到的参数等于运行时发现不了的能力**）。

后四条都是从真实失败里补出来的，见 DESIGN_NOTES.md。

用法：``python3 scripts/validate.py [技能目录]``；退出码 0 通过、1 有问题。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from pathlib import Path

# 六项验收契约的名称。改名等于换掉一项验收标准，必须显式。
CONTRACTS = ("入口唯一", "单品牌隔离", "动态访谈", "责任隔离", "输出有效", "学习不僵化")

ROLES = (
    "evidence-researcher", "editorial-strategist", "professional-writer",
    "source-verifier", "editorial-adversary", "feedback-attributor",
)

# 写作者必须拿不到检索工具——写作阶段重新翻库会让"取舍问题"和"表达问题"永久混在一起
RETRIEVAL_TOOLS = ("Grep", "Glob", "WebSearch", "WebFetch", "Bash", "PowerShell")

CLAUDE_SKILL_FIELDS = {
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "allowed-tools", "disallowed-tools",
    "model", "effort", "context", "agent", "background", "hooks", "paths", "shell",
    "metadata", "license", "compatibility",
}
CLAUDE_AGENT_FIELDS = {
    "name", "description", "model", "effort", "maxTurns", "tools", "disallowedTools",
    "skills", "memory", "background", "isolation",
}


def frontmatter(path: Path) -> tuple[dict[str, str], str]:
    """极简 frontmatter 解析：只取顶层 ``键: 值``，够做规范检查。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end < 0:
        return {}, text
    head, body = text[3:end], text[end + 4:]
    data: dict[str, str] = {}
    for line in head.splitlines():
        if line.startswith((" ", "\t", "#")) or ":" not in line:
            continue
        key, _, value = line.partition(":")
        data[key.strip()] = value.strip().strip("'\"")
    return data, body


def check(root: Path) -> list[str]:
    """返回问题列表；空列表表示通过。"""
    problems: list[str] = []

    def need(rel: str) -> Path | None:
        path = root / rel
        if not path.exists():
            problems.append(f"缺文件：{rel}")
            return None
        return path

    # --- 入口 ---
    skill = need("SKILL.md")
    if skill is not None:
        meta, body = frontmatter(skill)
        if meta.get("name") != root.name:
            problems.append(f"SKILL.md 的 name「{meta.get('name')}」与目录名「{root.name}」不一致")
        if meta.get("disable-model-invocation") != "true":
            problems.append(
                "SKILL.md 缺 disable-model-invocation: true —— "
                "「普通自然语言不承诺自动触发」只写在正文里是无效约束，正文要等调用之后才被读到"
            )
        if not meta.get("description"):
            problems.append("SKILL.md 缺 description")
        unknown = sorted(set(meta) - CLAUDE_SKILL_FIELDS)
        if unknown:
            problems.append(f"SKILL.md 含 Claude Code 未声明的 frontmatter 字段：{unknown}")
        if "/blueink " in body or "`/blueink`" in body.replace("`/blueink-suite`", ""):
            pass  # 正文里提到旧入口是为了说明不兼容，不算问题
        for name in CONTRACTS:
            if name not in body and name not in (root / "references/troubleshooting.md").read_text(
                encoding="utf-8"
            ):
                problems.append(f"六项验收契约里的「{name}」在文档中找不到")

    # Claude Code 当前支持插件根级单个 SKILL.md。再放一个同名 commands/ 入口会
    # 形成两个 /blueink-suite 定义，不是“双保险”，而是解析顺序依赖。
    if (root / "commands" / "blueink-suite.md").exists():
        problems.append("commands/blueink-suite.md 与根级 SKILL.md 重名——只保留一个入口")

    compatibility = frontmatter(skill)[0].get("compatibility", "") if skill else ""
    if "Claude Code only" not in compatibility:
        problems.append("SKILL.md compatibility 没有声明 Claude Code only")
    if (root / "AGENTS.md").exists():
        problems.append("仍有 AGENTS.md——当前包声明只适配 Claude Code，不再维护跨工具侧门")

    # --- 角色契约 ---
    agents = root / "agents"
    if not agents.is_dir():
        problems.append("缺 agents/ 目录")
    else:
        present = {p.stem for p in agents.glob("*.md")}
        for role in ROLES:
            if role not in present:
                problems.append(f"缺角色契约：agents/{role}.md")
        writer = agents / "professional-writer.md"
        if writer.is_file():
            meta, _ = frontmatter(writer)
            tools = meta.get("tools", "")
            bad = [t for t in RETRIEVAL_TOOLS if t in tools]
            if bad:
                problems.append(
                    f"写作者被授予了检索工具（{'、'.join(bad)}）——"
                    f"写作阶段重新翻库会让取舍问题与表达问题无法区分"
                )
        for role in ROLES:
            path = agents / f"{role}.md"
            if not path.is_file():
                continue
            meta, _ = frontmatter(path)
            if not meta.get("description"):
                problems.append(f"agents/{role}.md 缺 description")
            if meta.get("name") != role:
                problems.append(
                    f"agents/{role}.md 的 name 应为 {role}，由插件命名空间生成"
                    f" blueink-suite:{role}；当前是 {meta.get('name')}"
                )
            unknown = sorted(set(meta) - CLAUDE_AGENT_FIELDS)
            if unknown:
                problems.append(
                    f"agents/{role}.md 含 Claude Code 未声明的 frontmatter 字段：{unknown}"
                )
            if "tools" not in meta:
                problems.append(
                    f"agents/{role}.md 缺 tools —— 没有工具白名单时角色边界只能靠自律"
                )

    # --- 文档断链 ---
    # 只检查看起来是"技能内相对路径"的引用。文档里也会出现知识库里的路径
    # （例如知识库里混进来的技能包模板），那些不该被当成断链——判据是它前面还有别的目录层级。
    pattern = re.compile(
        r"(?<![A-Za-z0-9_\-./])((?:references|agents|assets|scripts|evals|commands)/[A-Za-z0-9_\-./]+)`"
    )
    for doc in sorted(root.rglob("*.md")):
        if ".blueink" in doc.parts:
            continue
        text = doc.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            rel = match.group(1)
            if not (root / rel).exists():
                problems.append(f"{doc.relative_to(root)} 引用了不存在的 {rel}")

    # --- 技能本体不含绝对路径 ---
    # 「不内嵌品牌语料」按路径判定，实现在 security_scan.py；这里只查绝对路径，
    # 因为它让技能无法整目录复制给另一位老师。
    for script in sorted((root / "scripts").glob("*.py")):
        text = script.read_text(encoding="utf-8")
        for number, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith(("#", '(r"')):
                continue
            for hit in re.findall(r"[\"'](/Users/[^\"']+)[\"']", line):
                problems.append(f"scripts/{script.name}:{number} 硬编码了本机绝对路径：{hit}")

    # --- 评测规格 ---
    ev = root / "evals" / "blueink-suite.eval.md"
    if ev.is_file():
        blocks = re.findall(r"```json\n(.*?)```", ev.read_text(encoding="utf-8"), re.S)
        if not blocks:
            problems.append("evals/blueink-suite.eval.md 里没有 JSON 规格块")
        else:
            try:
                json.loads(blocks[-1])
            except json.JSONDecodeError as exc:
                problems.append(f"评测规格 JSON 无法解析：{exc}")

    problems += review_due(root)
    problems += task_order_fields(root)
    problems += workspace_template_matches(root)
    problems += claude_only_distribution(root)
    problems += correction_target(root)
    problems += documented_flags(root)
    return problems


def workspace_template_matches(root: Path) -> list[str]:
    """注释模板的语料布局必须与 ``bind --create`` 的真实骨架一致。"""
    sys.path.insert(0, str(root / "scripts"))
    try:
        import miniyaml  # noqa: PLC0415
        import workspace  # noqa: PLC0415
        data = miniyaml.load_file(root / "assets" / "workspace.template.yaml")
    except Exception as exc:  # noqa: BLE001
        return [f"无法核对工作空间模板：{exc}"]
    actual = data.get("corpus_layout") if isinstance(data, dict) else None
    if actual != workspace.DEFAULT_CORPUS_LAYOUT:
        return [
            "assets/workspace.template.yaml 的 corpus_layout 与 bind --create 不一致："
            f"模板 {actual!r}，代码 {workspace.DEFAULT_CORPUS_LAYOUT!r}"
        ]
    return []


def claude_only_distribution(root: Path) -> list[str]:
    """安装说明与脚本不得继续承诺其它宿主，也不得带作者机器路径。"""
    problems: list[str] = []
    active = [root / "README.md", root / "SKILL.md", *sorted((root / "references").glob("*.md")),
              *sorted((root / "assets").glob("*.md"))]
    joined = "\n".join(p.read_text(encoding="utf-8") for p in active if p.is_file())
    for forbidden in ("/Users/", "/tmp/", "~/.codex/skills", "~/.agents/skills", "Codex CLI"):
        if forbidden in joined:
            problems.append(f"Claude Code 专用文档仍含平台／宿主特定地址：{forbidden}")
    shell = (root / "install.sh").read_text(encoding="utf-8")
    powershell = (root / "install.ps1").read_text(encoding="utf-8")
    for forbidden in ("--codex", "--to", "AGENTS_ROOT"):
        if forbidden in shell or forbidden in powershell:
            problems.append(f"安装脚本仍含跨宿主分支：{forbidden}")
    try:
        market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"marketplace.json 无法解析：{exc}")
    else:
        if market.get("$schema"):
            problems.append("marketplace.json 不应保留未经验证的 $schema 地址")
    return problems


# 不要求写进文档的参数，逐个给理由。**这是一份显式豁免清单，不是一个开关**：
# 往这里加一项要在 diff 里被看见，而"文档里找不到的参数一律放过"不会。
FLAG_EXEMPTIONS = {
    "--json": "输出形态，不改变任何语义；每个子命令都有，写进文档只会稀释真正要读的内容",
}


def documented_flags(root: Path) -> list[str]:
    """`blueink.py` 的每个参数都必须在文档里出现过，或在豁免清单里。

    这一条守的是一类真实失败：新增 `--attach` 之后，`SKILL.md` 写了"登记方式见
    《工作空间与索引》"，而那份文件里根本没有 `--attach`。模型只能 `grep scripts/*.py`
    去反查机制。**按需读取的前提是被指向的那一份真的写了那件事**——路由本身不产生内容。

    当时修的是那一个实例。这道门堵的是那一类：一个没被任何文档提到的参数，等于一个
    运行时发现不了的能力。
    """
    entry = root / "scripts" / "blueink.py"
    if not entry.is_file():
        return ["缺 scripts/blueink.py"]
    try:
        tree = ast.parse(entry.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [f"scripts/blueink.py 无法解析：{exc}"]

    flags: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                        and arg.value.startswith("--"):
                    flags.add(arg.value)

    globs = ("*.md", "references/*.md", "agents/*.md", "assets/*.md",
             "commands/*.md", "evals/*.md")
    docs = "\n".join(
        path.read_text(encoding="utf-8")
        for pattern in globs for path in sorted(root.glob(pattern))
    )
    return [
        f"参数 {flag} 没有出现在任何文档里——运行时发现不了的能力等于不存在"
        f"（确实不需要写文档时，加进 validate.py 的 FLAG_EXEMPTIONS 并写清理由）"
        for flag in sorted(flags) if flag not in docs and flag not in FLAG_EXEMPTIONS
    ]


def correction_target(root: Path) -> list[str]:
    """`evolve.py --correct` 追加纠正的目标标题必须还在。

    这一条守的是一类真实回归：`SKILL.md` 里的 `## Gotchas` 一旦被删掉，
    而 `--correct` 的实现只按标题匹配——找不到就在文件末尾自己造一个。于是一个
    刻意精简过的首读层会随着每次纠正慢慢重新长成操作手册，而且没有任何报错。
    """
    target = root / "references" / "troubleshooting.md"
    if not target.is_file():
        return ["缺 references/troubleshooting.md——evolve --correct 没有落点"]
    if not re.search(r"^#{1,6}[ \t]+Gotchas\b", target.read_text(encoding="utf-8"), re.M | re.I):
        return ["references/troubleshooting.md 缺 `## Gotchas` 标题——"
                "evolve --correct 会改在文件末尾自己造一节，且不报错"]
    skill = root / "SKILL.md"
    if skill.is_file() and re.search(
            r"^#{1,6}[ \t]+Gotchas\b", skill.read_text(encoding="utf-8"), re.M | re.I):
        return ["SKILL.md 又出现了 `## Gotchas` 标题——首读层是方法论，"
                "使用中捕获的具体现象应落在 references/troubleshooting.md"]
    return []


# 任务单的必填字段。子智能体拿不到其中任何一个，就只能在项目目录里逐个猜路径——
# 缺了它，一个策略师实例可能连试六个不存在的路径才放弃。字段写在两个文件里
# （《编排协议》的示例和 assets 模板），两处发散时后写的那一份会静默失效。
TASK_ORDER_FIELDS = (
    "run_id", "task_id", "role", "brand", "kb_root", "skill_root",
    "project_root", "python", "cli", "expect",
)


def task_order_fields(root: Path) -> list[str]:
    """《编排协议》的任务单示例与 assets 模板必须都声明全部必填字段。"""
    problems: list[str] = []
    for rel in ("references/orchestration-protocol.md", "assets/task-order-template.md"):
        path = root / rel
        if not path.is_file():
            problems.append(f"缺 {rel}")
            continue
        text = path.read_text(encoding="utf-8")
        for field in TASK_ORDER_FIELDS:
            if f"{field}:" not in text:
                problems.append(
                    f"{rel} 的任务单里缺 {field}——子智能体拿不到它就只能猜路径"
                )
    return problems


def review_due(root: Path, today: date | None = None) -> list[str]:
    """`last_reviewed` 是否已经超过 `review_interval_days`。

    这原来是四个模块（review_staleness / dependency_health / schema_drift /
    staleness_check，共 695 行）做的一件事。另外三个模块探测的是本技能根本没有
    声明的 HTTP 依赖和 API schema——通用技能工厂需要它们，BlueInk 不需要。
    留下的只有真正会过期的那一项：复审日期。
    """
    skill = root / "SKILL.md"
    if not skill.is_file():
        return []
    head = skill.read_text(encoding="utf-8").split("\n---", 1)[0]
    reviewed = _subfield(head, "metadata", "last_reviewed")
    interval = _subfield(head, "metadata", "review_interval_days")
    if not reviewed or not interval:
        return ["SKILL.md frontmatter 缺 metadata.last_reviewed 或 review_interval_days"]
    try:
        reference = date.fromisoformat(reviewed)
        days = int(interval)
    except ValueError:
        return [f"SKILL.md 的 last_reviewed／review_interval_days 无法解析：{reviewed!r} / {interval!r}"]
    overdue = (today or date.today()) - reference
    if overdue.days > days:
        return [f"SKILL.md 已超过复审周期：{reviewed} 起 {overdue.days} 天 > {days} 天"]
    return []


def _subfield(head: str, parent: str, key: str) -> str:
    """从 frontmatter 文本里读 ``parent.key``。缩进块里的一行标量，够用就好。"""
    inside = False
    for line in head.splitlines():
        if not line.startswith((" ", "\t")):
            inside = line.split(":", 1)[0].strip() == parent
            continue
        if inside and line.strip().split(":", 1)[0].strip() == key:
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="blueink-suite 规范自检")
    parser.add_argument("skill_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()
    root = Path(args.skill_dir).resolve()
    print(f"校验目录：{root}")
    problems = check(root)
    if problems:
        print(f"状态：INVALID（{len(problems)} 项）")
        for item in problems:
            print(f"  [错误] {item}")
        return 1
    print("状态：VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
