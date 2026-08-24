#!/usr/bin/env python3
"""规范自检：这个技能包自己是否还满足它声称的那些契约。

官方 ``agent-skill-creator`` 的 validate 检查的是通用技能规范（frontmatter 齐不齐、
目录名对不对）。这里检查的是**本技能特有的、一旦破掉就会静默失效的东西**：

- 入口是否真的机械关闭了模型自动调用（散文声明不算）；
- 根级 ``agents/`` 是否不存在，默认快线与三份条件指导是否完整；
- 文档里引用的文件是否真的存在（断链的"按需读取"等于没有那一节）；
- 技能本体是否混进了品牌语料或绝对路径（技能必须能整目录复制给另一位老师）；
- 五项验收契约的名称有没有被悄悄改掉；
- 复审日期是否已经越过声明的复审周期；
- 默认生成是否明确单智能体、强制方向确认、先交可修改初稿并只保留四份产物；
- `evolve --correct` 的落点标题是否还在，以及它有没有跑回 `SKILL.md`（首读层是方法论）；
- `blueink.py` 的每个参数是否在文档里出现过（**没被任何文档提到的参数等于运行时发现不了的能力**）。

产品特有约束与验证边界见 DESIGN_NOTES.md。

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

# 五项验收契约的名称。改名等于换掉一项验收标准，必须显式。
CONTRACTS = ("入口唯一", "单品牌隔离", "动态访谈", "阶段边界", "输出有效")

GUIDES = {
    "generate": "默认生成快线",
    "research": "条件证据研究",
    "feedback": "真实反馈",
    "troubleshooting": "问题定位",
}

CLAUDE_SKILL_FIELDS = {
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "allowed-tools", "disallowed-tools",
    "model", "effort", "context", "agent", "background", "hooks", "paths", "shell",
    "metadata", "license", "compatibility",
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
        if "有附件时不要先跑 `status`" not in body:
            problems.append(
                "SKILL.md 缺附件优先短路 —— 已给附件的任务可能先跑 status 并被误判成绑定失败"
            )
        for name in CONTRACTS:
            if name not in body and name not in (root / "references/troubleshooting.md").read_text(
                encoding="utf-8"
            ):
                problems.append(f"五项验收契约里的「{name}」在文档中找不到")

    # Claude Code 当前支持插件根级单个 SKILL.md。再放一个同名 commands/ 入口会
    # 形成两个 /blueink-suite 定义，不是“双保险”，而是解析顺序依赖。
    if (root / "commands" / "blueink-suite.md").exists():
        problems.append("commands/blueink-suite.md 与根级 SKILL.md 重名——只保留一个入口")

    compatibility = frontmatter(skill)[0].get("compatibility", "") if skill else ""
    if "Claude Code only" not in compatibility:
        problems.append("SKILL.md compatibility 没有声明 Claude Code only")
    if (root / "AGENTS.md").exists():
        problems.append("仍有 AGENTS.md——当前包声明只适配 Claude Code，不再维护跨工具侧门")

    # --- 单智能体快线契约 ---
    agents = root / "agents"
    if agents.exists():
        problems.append("仍有根级 agents/——Claude Code 会把它注册成子智能体，单智能体架构不成立")
    if skill is not None:
        skill_text = skill.read_text(encoding="utf-8")
        if "不调用 Agent、Task、后台智能体、独立 `claude` 进程或其他模型会话" not in skill_text:
            problems.append("SKILL.md 缺单智能体硬约束")

    guide_root = root / "references"
    for guide, title in GUIDES.items():
        path = guide_root / f"{guide}.md"
        if not path.is_file():
            problems.append(f"缺运行指导：references/{guide}.md")
            continue
        meta, body = frontmatter(path)
        if meta:
            problems.append(
                f"references/{guide}.md 含 frontmatter——运行指导不得注册成独立入口"
            )
        if f"# {title}" not in body:
            problems.append(f"references/{guide}.md 缺标题「{title}」")

    generate = guide_root / "generate.md"
    if generate.is_file():
        text = generate.read_text(encoding="utf-8")
        for marker, message in (
            ("🔴 CHECKPOINT", "默认生成缺成稿前方向确认检查点"),
            ("生成任务不允许零轮访谈", "默认生成仍允许跳过访谈"),
            ("最多 12 条", "扩展研究没有限制事实原子数量"),
            ("handoff --run", "默认生成没有在完整初稿后立即 handoff"),
            ("不得再写、编辑或覆盖 `draft.md`", "初稿交付后仍可能被当前智能体覆盖"),
            ("不设置硬时限", "默认生成把优化目标写成了硬超时"),
            ("这不是独立审查", "默认生成把同一上下文复核包装成独立审查"),
            ("不在写作时重新检索、换主线或补事实", "默认生成允许成稿时重新选素材"),
            ("run.json", "默认生成缺新版聚合运行记录"),
            ("delivery.md", "默认生成缺业务交付文件"),
            ("最终回复必须直接输出 `delivery.md` 的", "默认生成允许最终回复只给交付路径"),
        ):
            if marker not in text:
                problems.append(message)
    if (root / "references" / "stages").exists():
        problems.append("仍有 references/stages/——默认生成已经合并为一份快线指导")

    # --- 文档断链 ---
    # 只检查看起来是"技能内相对路径"的引用。文档里也会出现知识库里的路径
    # （例如知识库里混进来的技能包模板），那些不该被当成断链——判据是它前面还有别的目录层级。
    pattern = re.compile(
        r"(?<![A-Za-z0-9_\-./])((?:references|assets|scripts|evals|commands)/[A-Za-z0-9_\-./]+)`"
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
    problems += guide_contract(root)
    problems += workspace_template_matches(root)
    problems += claude_only_distribution(root)
    problems += product_voice(root)
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
    active = [root / "README.md", root / "SKILL.md", *sorted((root / "references").rglob("*.md")),
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


def product_voice(root: Path) -> list[str]:
    """用户会读到的文档只描述当前产品，不写实现演进过程。"""
    patterns = (
        r"破坏性变更", r"第\s*\d+\s*轮(?:审查|修改|迭代)",
        r"(?:旧版|老版本)(?:逻辑|实现)", r"(?:已经|已)(?:删除|移除)",
        r"(?:之前|此前)版本", r"迁移自旧版", r"曾经实现",
        r"什么被保留，什么不再声称", r"不再声称", r"兼容既有", r"兼容审计",
        r"被否掉的(?:方案|替代方案)", r"旧前提|新前提", r"中转站",
        r"一次真实(?:失败|教训)", r"从一次真实运行", r"真实教训",
        r"这里合并的是.+不是删除", r"(?:实现|架构)(?:演进|迁移)",
    )
    docs = [
        root / "SKILL.md", root / "README.md", root / "CHANGELOG.md",
        root / "DESIGN_NOTES.md", root / "DECISIONS.md", root / "EVOLUTION.md",
        root / ".claude-plugin" / "plugin.json",
        root / ".claude-plugin" / "marketplace.json",
        *sorted((root / "references").rglob("*.md")),
        *sorted((root / "assets").glob("*.md")),
        *sorted((root / "assets").glob("*.json")),
        *sorted((root / "assets").glob("*.yaml")),
        *sorted((root / "evals").glob("*.md")),
    ]
    problems: list[str] = []
    for path in docs:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(
                    f"{path.relative_to(root)}:{line} 使用了实现演进口吻：{match.group(0)}"
                )
    return problems


# 不要求写进文档的参数，逐个给理由。**这是一份显式豁免清单，不是一个开关**：
# 往这里加一项要在 diff 里被看见，而"文档里找不到的参数一律放过"不会。
FLAG_EXEMPTIONS = {
    "--json": "输出形态，不改变任何语义；每个子命令都有，写进文档只会稀释真正要读的内容",
}


def documented_flags(root: Path) -> list[str]:
    """`blueink.py` 的每个参数都必须在文档里出现过，或在豁免清单里。

    **按需读取的前提是被指向的文档真的写了对应参数**——路由本身不产生内容。
    一个没被任何文档提到的参数，等于一个运行时发现不了的能力。
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

    globs = ("*.md", "references/**/*.md", "assets/*.md",
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
    """``evolve.py --correct`` 的详细落点与条件路由必须同时存在。"""
    target = root / "references" / "troubleshooting.md"
    if not target.is_file():
        return ["缺 references/troubleshooting.md——evolve --correct 没有落点"]
    if not re.search(r"^#{1,6}[ \t]+Gotchas\b", target.read_text(encoding="utf-8"), re.M | re.I):
        return ["references/troubleshooting.md 缺 `## Gotchas` 标题——"
                "evolve --correct 会改在文件末尾自己造一节，且不报错"]
    skill = root / "SKILL.md"
    if not skill.is_file():
        return ["缺 SKILL.md"]
    skill_text = skill.read_text(encoding="utf-8")
    if "references/troubleshooting.md" not in skill_text:
        return ["SKILL.md 没有条件路由到 references/troubleshooting.md"]
    return []


def guide_contract(root: Path) -> list[str]:
    """根入口只默认加载 generate，其余三份指导必须条件触发。"""
    skill = root / "SKILL.md"
    if not skill.is_file():
        return ["缺 SKILL.md"]
    text = skill.read_text(encoding="utf-8")
    problems: list[str] = []
    if "开启运行后只读取" not in text or "references/generate.md" not in text:
        problems.append("SKILL.md 没有把 generate.md 设为唯一默认运行指导")
    for guide in ("research", "feedback", "troubleshooting"):
        if f"references/{guide}.md" not in text:
            problems.append(f"SKILL.md 没有条件路由到 references/{guide}.md")
    for legacy in ("stage-execution-protocol.md", "references/stages/"):
        if legacy in text:
            problems.append(f"SKILL.md 仍路由到旧阶段资产：{legacy}")
    return problems


def review_due(root: Path, today: date | None = None) -> list[str]:
    """检查 `last_reviewed` 是否超过 `review_interval_days`。"""
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
