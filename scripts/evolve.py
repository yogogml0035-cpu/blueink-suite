#!/usr/bin/env python3
"""
Evolve loop shipped inside every generated skill as scripts/evolve.py.

One command that closes the maintenance loop from the skill's own root:

    python3 scripts/evolve.py            # detect -> record -> re-verify
    python3 scripts/evolve.py --judge    # 上游能力：给 llm-judge 判据打分。**本技能用不上**——
                                        # 评测规格刻意不含 llm-judge 检查（审计结论的结构、
                                        # 自洽性和可定位性都能用代码判定，用模型评分只会引入
                                        # 一个不确定的环节），规格里也没有 judge 块，所以这个
                                        # 参数会明确报错而不是静默通过。
    python3 scripts/evolve.py --correct "<what the skill got wrong>"

Steps (default mode) — 与下面 `steps` 列表逐项对应：
  1. test_state          状态层回归：绑定拦截、指令产物隔离、Office 抽取、符号链接
                         跳过、URL 白名单、置信度升降、审计定位、附件登记
  2. self_check          自证门：版本底线 / 声明一致性 / 变异承重
  3. check_pipeline      管线接线：全部脚本可编译、依赖已声明、单一入口存在
  4. validate            规范自检：入口机械化、六份阶段指导、文档断链、契约名称、复审周期
  5. security_scan       安全扫描：越界执行、外发、凭据、对源库的写操作
  6. run_evals --rollout 审计器夹具 + 基线全等比对

前三步排在最前：它们守的边界失效时不会自然报错，而且执行成本最低。

`--correct` is a different kind of input, and the most valuable one this loop
takes. Steps 1 and 2 catch what a machine can check: dates, reachability, output
drift. They cannot catch the class of knowledge that only exists in someone's
head -- the region that files late, the code that means something local, the
month the process runs differently.

That knowledge is not obtainable by asking up front. People cannot articulate a
workflow they run from muscle memory, which is why this factory reads artifacts
instead of interviewing. But the same person who could not describe the rule will
recognize its violation instantly, the moment the skill produces something wrong.
`--correct` is the capture point for that moment: it takes the sentence they would
have said out loud, writes it verbatim to EVOLUTION.md as evidence, and adds it to
the skill's `## Gotchas` so the next run already knows.

Exit codes:
    0 - fresh and green (or: correction recorded)
    1 - one or more steps failed; EVOLUTION.md holds the evidence
    2 - bad usage
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GOTCHAS_HEADING = re.compile(r"^([ \t]{0,3})(#{1,6})[ \t]+gotchas\b.*$", re.IGNORECASE | re.MULTILINE)
# A placeholder body meaning "there are none yet" -- replaced rather than appended to.
NONE_KNOWN = re.compile(r"^\s*(none known|none|n/a)\.?\s*$", re.IGNORECASE)
# Sections a Gotchas block should precede when one has to be created from scratch.
FALLBACK_ANCHORS = ("## Keywords", "## Usage Examples", "## References", "## Anti-goals")


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_correction(skill_dir: Path, text: str) -> list[str]:
    """Write a user correction to EVOLUTION.md and the troubleshooting Gotchas section.

    Verbatim on purpose. The wording a user reaches for when something is wrong
    carries detail a paraphrase drops, and this runs unattended -- rewriting it
    into the wrong-assumption/real-behavior house style is a judgment call for
    whoever next edits the skill, not for this script.

    Args:
        skill_dir: The skill's root (the directory holding SKILL.md).
              The correction lands in references/troubleshooting.md, not SKILL.md.
        text: The correction, as the user phrased it.

    Returns:
        Human-readable lines describing what changed, for printing.

    Raises:
        ValueError: If the text is blank or SKILL.md is missing.
    """
    text = " ".join(text.split())
    if not text:
        raise ValueError("correction text is empty")

    # 刻意不是 SKILL.md：首读层是方法论，把每次使用中发现的具体现象往那里堆，
    # 几个月后它就会变成一份操作手册，而首读层必须保持是方法论。落点是
    # 《问题定位手册》，遇到行为不符合预期时人本来就会来读那一份。
    # validate.py 的 correction_target() 守着这个文件里的 `## Gotchas` 标题还在。
    target = skill_dir / "references" / "troubleshooting.md"
    if not target.exists():
        raise ValueError(f"references/troubleshooting.md not found in {skill_dir}")

    stamp = _utc_stamp()
    done: list[str] = []

    # 1. EVOLUTION.md -- the audit trail. Append-only, never rewritten.
    evolution = skill_dir / "EVOLUTION.md"
    if not evolution.exists():
        evolution.write_text(
            "# Evolution log\n\nAppended automatically by scripts/run_evals.py "
            "(and scripts/evolve.py) when a check fails. Each entry is the raw "
            "evidence for a fix/regenerate step.\n\n",
            encoding="utf-8",
        )
    with evolution.open("a", encoding="utf-8") as fh:
        fh.write(
            f"## {stamp} — correction from use\n\n"
            f"Reported while using the skill, not caught by any automated check.\n\n"
            f"> {text}\n\n"
            f"Added to the `references/troubleshooting.md` `## Gotchas` section.\n\n"
        )
    done.append(f"EVOLUTION.md  <- correction recorded ({stamp})")

    # 2. troubleshooting.md ## Gotchas -- the part read when behaviour surprises someone.
    content = target.read_text(encoding="utf-8")
    bullet = f"- {text}"
    match = GOTCHAS_HEADING.search(content)

    if match:
        level = len(match.group(2))
        body_start = match.end()
        # The section runs to the next heading at the same level or higher.
        following = re.compile(rf"^[ \t]{{0,3}}#{{1,{level}}}[ \t]+", re.MULTILINE)
        nxt = following.search(content, body_start)
        body_end = nxt.start() if nxt else len(content)
        body = content[body_start:body_end]

        if all(NONE_KNOWN.match(ln) or not ln.strip() for ln in body.splitlines()):
            new_body = f"\n\n{bullet}\n\n"          # replace the placeholder
            note = "replaced 'None known'"
        else:
            new_body = body.rstrip("\n") + f"\n{bullet}\n\n"
            note = "appended"
        content = content[:body_start] + new_body + content[body_end:]
    else:
        section = f"## Gotchas\n\n{bullet}\n\n"
        anchor = next((a for a in FALLBACK_ANCHORS if f"\n{a}" in content), None)
        if anchor:
            content = content.replace(f"\n{anchor}", f"\n{section}{anchor}", 1)
        else:
            content = content.rstrip("\n") + f"\n\n{section}"
        note = "created the section"

    target.write_text(content, encoding="utf-8")
    done.append(f"references/troubleshooting.md  <- ## Gotchas ({note})")
    return done


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(__file__).resolve().parent.parent

    if "--correct" in args:
        index = args.index("--correct")
        text = " ".join(args[index + 1:])
        if not text.strip():
            print(
                'usage: python3 scripts/evolve.py --correct "<what the skill got wrong>"',
                file=sys.stderr,
            )
            return 2
        try:
            for line in record_correction(root, text):
                print(line)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print("\nnext: run scripts/evolve.py to re-verify, and reword the entry in the")
        print("house style (wrong assumption -> real behavior) next time you edit the skill")
        return 0

    judge = ["--judge"] if "--judge" in args else []

    steps = [
        # 先跑最便宜也最容易抓到真问题的一步：状态层回归。绑定拦截、指令产物隔离、
        # URL 白名单、置信度规则破掉之后没人会立刻发现，所以它排在最前面。
        ("state", [sys.executable, "scripts/test_state.py"]),
        # 自证门紧跟其后，检查 Python 底线、文档项数与子命令边界。
        ("self", [sys.executable, "scripts/self_check.py"]),
        # 管线检查属于发布门，必须和状态层与自证门一起执行。
        # 一个不被执行的检查器等于不存在。
        ("pipeline", [sys.executable, "scripts/check_pipeline.py", str(root)]),
        ("validate", [sys.executable, "scripts/validate.py", str(root)]),
        ("security", [sys.executable, "scripts/security_scan.py", str(root)]),
        ("evals", [
            sys.executable, "scripts/run_evals.py", str(root), "--rollout", *judge,
        ]),
    ]

    failures: list[tuple[str, int]] = []
    for name, cmd in steps:
        print(f"== evolve: {name} ==")
        proc = subprocess.run(cmd, cwd=root)  # noqa: S603
        if proc.returncode != 0:
            failures.append((name, proc.returncode))

    if failures:
        print("\nevolve: FAILED")
        for name, rc in failures:
            print(f"  - {name} (exit {rc})")
        print(
            "next: fix the findings, then re-run scripts/evolve.py until clean"
        )
        return 1

    print("\nevolve: all checks fresh and green")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
