#!/usr/bin/env python3
"""自证门：把这个技能**对外声明过的东西**变成机械检查。

重点防三类静默漂移：运行底线与实际解释器能力不一致；文档数字与当前检查结果不一致；
命令面超出文案生成的产品边界。

对应三道门：

    --compat    版本底线：静态扫描高于底线的 stdlib API 与语法；本机存在 3.9
                解释器时，用它真跑一遍状态层与管线检查
    --claims    声明一致性：文档里的数字、子命令面、边界清单、留存策略、脚本
                清单必须与代码实际一致，**双向**核对
    --mutation  变异承重：真的往技能副本里注入七个已知失败形态，断言每一个都
                会让指定检查转红

不给参数就三道全跑。退出码 0 表示全过。

**为什么要有变异门。** "我们做过变异测试"写在 README 里只是一句话。一段声称能
抓住错误的检查，在它从没被喂过错误输入的情况下，和一段 ``return True`` 无法区
分。变异门每次都真的把错误注进去，看检查会不会红。它锁住四条最实质的保证：增量
索引按内容哈希而不是 size+mtime（后者会静默漏更新）；混进来的技能包按目录整棵子树
隔离而不是只挡入口文件（后者会让外部固定模板接管本次判断）；访谈停止理由必须指名
动态充分性维度（否则一句"信息已经足够"照样过审）；同一个问题问两遍必须被判违约
（否则老师会开始敷衍）。这四条一旦退化，症状都是静默的——所以只能靠变异门守。

用法：

    python3 scripts/self_check.py
    python3 scripts/self_check.py --claims --json
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

# 声明的运行底线。改这里必须同时改 README 与 requirements.txt，--claims 会核对。
FLOOR = (3, 9)

# 高于底线才有的 stdlib API。只列**属性访问**与**模块名**，语法特征交给
# ast.parse(feature_version=...) 判定，那比维护一张语法表可靠。
NEWER_THAN_FLOOR: dict[str, tuple[int, int]] = {
    "sys.stdlib_module_names": (3, 10),
    "itertools.pairwise": (3, 10),
    "typing.TypeGuard": (3, 10),
    "datetime.UTC": (3, 11),
    "typing.Self": (3, 11),
    "typing.LiteralString": (3, 11),
    "asyncio.TaskGroup": (3, 11),
    "enum.StrEnum": (3, 11),
    "itertools.batched": (3, 12),
    "pathlib.Path.walk": (3, 12),
}
NEWER_MODULES: dict[str, tuple[int, int]] = {
    "tomllib": (3, 11),
    "asyncio.taskgroups": (3, 11),
}
NEWER_BUILTINS: dict[str, tuple[int, int]] = {
    "ExceptionGroup": (3, 11),
    "BaseExceptionGroup": (3, 11),
    "aiter": (3, 10),
    "anext": (3, 10),
}

# 取自 agent-skill-creator 的外来件，保持英文原样以便与上游重新同步。
UPSTREAM_SCRIPTS = {
    "run_evals.py", "evolve.py", "check_pipeline.py",
}

# 自研脚本白名单。README 只负责安装；复杂度边界由这里直接与 scripts/ 双向核对。
OWN_SCRIPTS = {
    "audit.py", "blueink.py", "index_kb.py", "memory.py", "miniyaml.py",
    "official.py", "retrieve.py", "run_pipeline.py", "run_record.py",
    "security_scan.py", "self_check.py", "test_state.py", "validate.py",
    "workspace.py",
}

# 方法论第八节声明的边界。每一条都必须同时出现在《方法论内核》与设计说明中，
# 少一处就说明其中一份维护文档已经开始许诺技能做不到（或不该做）的事。
BOUNDARY_KEYWORDS = ("改稿编排", "版本回退", "Word 排版", "品类认证", "自然语言自动触发")

# 越界能力的命令名。这些名字一旦出现在子命令表里，就说明"能力边界是文案生成"
# 这条边界已经破了——而它是会话里被反复确认过的一条。
FORBIDDEN_SUBCOMMANDS = {"rework", "rollback", "revert", "revise", "restore", "version"}

# 五项验收契约的名称与顺序。审计器与评测规格必须一致，否则某项契约可以被悄悄改名。
CONTRACT_NAMES = ["入口唯一", "单品牌隔离", "动态访谈", "责任隔离", "输出有效"]

DOC_GLOBS = ("*.md", "references/*.md", "evals/*.md", "agents/*.md", "commands/*.md")

# 版本说明与使用反馈证据不是运行契约，项数一致性只核对产品与评测文档。
COUNT_EXEMPT_DOCS = {"CHANGELOG.md", "EVOLUTION.md"}

# 嵌套标记。评测规格里有一条检查会调用本脚本，本脚本的变异门又要调用评测 harness——
# 这是一条真实的环。看到这个标记就立刻退出，把环剪断在最外面一层。
NESTED_FLAG = "BLUEINK_SELF_CHECK_NESTED"


class Result:
    """一道门的结论。``skipped`` 是第一等公民：把"没查"写成"通过"是最危险的输出。"""

    def __init__(self, gate: str) -> None:
        self.gate = gate
        self.failures: list[str] = []
        self.skipped: list[str] = []
        self.notes: list[str] = []
        self.checked = 0

    def check(self, ok: bool, message: str) -> bool:
        self.checked += 1
        if not ok:
            self.failures.append(message)
        return ok

    def skip(self, message: str) -> None:
        self.skipped.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate, "checked": self.checked,
            "failed": self.failures, "skipped": self.skipped, "notes": self.notes,
        }


# --- 公共工具 ---------------------------------------------------------------


def script_files(root: Path) -> list[Path]:
    return sorted(p for p in (root / "scripts").glob("*.py"))


def doc_files(root: Path) -> list[Path]:
    seen: dict[Path, None] = {}
    for pattern in DOC_GLOBS:
        for path in sorted(root.glob(pattern)):
            seen.setdefault(path, None)
    return list(seen)


def _run(cmd: list[str], cwd: Path, nested: bool = False) -> subprocess.CompletedProcess:
    """跑一个子进程。``nested=True`` 时给子进程打上嵌套标记。

    标记是为了断开一条真实存在的环：评测规格里有一条检查会调用本脚本，而本脚本的
    变异门又要调用评测 harness。不断开的话第一次 ``evolve.py`` 就会无限递归下去，
    而症状是"卡住"，不是报错——最难查的那一类。
    """
    env = dict(os.environ)
    if nested:
        env[NESTED_FLAG] = "1"
    return subprocess.run(  # noqa: S603
        cmd, cwd=str(cwd), capture_output=True, text=True, timeout=600, env=env
    )


def _dotted(node: ast.AST) -> str:
    """把 ``a.b.c`` 形式的属性访问还原成字符串；其他形态返回空串。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


# --- 门一：版本底线 ---------------------------------------------------------


def gate_compat(root: Path) -> Result:
    res = Result("compat")
    floor_text = ".".join(str(n) for n in FLOOR)

    for path in script_files(root):
        rel = path.relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")

        # 语法：用底线版本的语法解析。match / except* 这类新语法在这里就会被拒。
        try:
            tree = ast.parse(source, feature_version=FLOOR)
        except SyntaxError as exc:
            res.check(False, f"{rel}: 语法高于 Python {floor_text} —— {exc.msg}")
            continue
        res.checked += 1

        # API：属性访问、模块导入、内置名三类
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                dotted = _dotted(node)
                for api, since in NEWER_THAN_FLOOR.items():
                    # 同时认全名（``sys.stdlib_module_names``）与去掉首段的形态
                    # （``from pathlib import Path`` 之后写的 ``Path.walk``）
                    if dotted in (api, api.split(".", 1)[-1]):
                        res.check(
                            False,
                            f"{rel}:{node.lineno} 用了 {api}，它要 Python "
                            f"{since[0]}.{since[1]}，底线是 {floor_text}",
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    full = f"{node.module}.{alias.name}"
                    since = NEWER_THAN_FLOOR.get(full)
                    if since:
                        res.check(
                            False,
                            f"{rel}:{node.lineno} 从 {node.module} 导入 {alias.name}，"
                            f"它要 Python {since[0]}.{since[1]}，底线是 {floor_text}",
                        )
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mods = (
                    [a.name for a in node.names] if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                for mod in mods:
                    since = NEWER_MODULES.get(mod.split(".")[0])
                    if since:
                        res.check(
                            False,
                            f"{rel}:{node.lineno} 导入 {mod}，它要 Python "
                            f"{since[0]}.{since[1]}，底线是 {floor_text}",
                        )
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                since = NEWER_BUILTINS.get(node.id)
                if since:
                    res.check(
                        False,
                        f"{rel}:{node.lineno} 用了内置 {node.id}，它要 Python "
                        f"{since[0]}.{since[1]}，底线是 {floor_text}",
                    )

    # 真机验证：静态扫描只挡得住已知名单，真跑一遍才挡得住名单之外的
    interpreter = _floor_interpreter()
    if interpreter is None:
        res.skip(
            f"本机没有 Python {floor_text} 解释器，跳过真机验证。"
            f"静态扫描只覆盖已登记的 API 名单，**不等于** {floor_text} 上一定能跑；"
            f"发布前请在 {floor_text} 环境补跑 test_state.py 与 check_pipeline.py"
        )
    else:
        res.note(f"真机验证用 {interpreter}")
        for script, args in (("test_state.py", []), ("check_pipeline.py", ["."])):
            proc = _run([interpreter, f"scripts/{script}", *args], root)
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            res.check(
                proc.returncode == 0,
                f"{script} 在 Python {floor_text} 上退出码 {proc.returncode}："
                f"{tail[-1] if tail else '无输出'}",
            )
    return res


def _floor_interpreter() -> str | None:
    """找一个版本恰好等于底线的解释器；找不到返回 None。"""
    floor_text = ".".join(str(n) for n in FLOOR)
    candidates = [f"python{floor_text}", "/usr/bin/python3", "python3"]
    for cand in candidates:
        exe = shutil.which(cand) if not cand.startswith("/") else (cand if Path(cand).exists() else None)
        if not exe:
            continue
        try:
            proc = subprocess.run(  # noqa: S603
                [exe, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0 and proc.stdout.strip() == floor_text:
            return exe
    return None


# --- 门二：声明一致性 -------------------------------------------------------


def gate_claims(root: Path) -> Result:
    res = Result("claims")
    docs = {path: path.read_text(encoding="utf-8") for path in doc_files(root)}

    _claim_counts(root, docs, res)
    _claim_subcommands(root, docs, res)
    _claim_boundaries(root, docs, res)
    _claim_python_floor(root, docs, res)
    _claim_retention(root, docs, res)
    _claim_script_inventory(root, docs, res)
    _claim_contract_names(root, docs, res)
    _report_composition(root, res)
    return res


def _numbered_items(line: str) -> list[tuple[int, int]]:
    """行内所有「N 项」，返回 (数字, 列号)。排除两种说的是别的量的写法：

    - ``× 5 项``：每个夹具几项，不是总数
    - ``5 项检查立刻转红``：变异测试里有几项转红，不是总数

    这两条排除避免把局部计数误判成当前总数。
    """
    out: list[tuple[int, int]] = []
    for match in re.finditer(r"(\d+) 项", line):
        if "转红" in line[match.end():match.end() + 12]:
            continue
        head = line[max(0, match.start() - 8):match.start()]
        if "×" in head:
            continue
        out.append((int(match.group(1)), match.start()))
    return out


# 一行里出现这些词，就认为附近的「N 项」在说这个主体的项数。一行同时谈两个主体
# 很常见（"状态层 54 项、审计器 6 个夹具 48 项"），所以按**离哪个主体近**归属，
# 而不是按行归属——按行归属会把夹具数当成状态层数报出来，那是误报。
COUNT_SUBJECTS: dict[str, tuple[str, ...]] = {
    "state": ("状态层", "state-layer", "test_state.py"),
    "fixture": ("夹具", "run_evals", "--rollout", "include-holdout"),
}


def _attributed_counts(line: str) -> list[tuple[str, int]]:
    """把行内每个「N 项」归属到最近的主体，返回 (主体, 数字)。

    距离按主体词的**整个跨度**算，不是只按它的起始位置——
    ``` `test_state.py` 79 项、`run_evals.py --rollout` 66 项 ``` 里，79 离
    `test_state.py` 的结尾很近但离它的开头很远，只按起始位置会把 79 判给夹具。
    """
    spans: list[tuple[str, int, int]] = [
        (subject, match.start(), match.end())
        for subject, markers in COUNT_SUBJECTS.items()
        for marker in markers
        for match in re.finditer(re.escape(marker), line)
    ]
    if not spans:
        return []

    def distance(span: tuple[str, int, int], at: int) -> int:
        _, start, end = span
        if start <= at <= end:
            return 0
        return start - at if at < start else at - end

    out: list[tuple[str, int]] = []
    for value, at in _numbered_items(line):
        subject = min(spans, key=lambda s: distance(s, at))[0]
        out.append((subject, value))
    return out


def _claim_counts(root: Path, docs: dict[Path, str], res: Result) -> None:
    """文档里写的项数必须等于测试真的跑出来的数。

    数字本身不重要；重要的是产品声明必须与可执行检查一致。

    判定按**整行 + 就近归属**而不是按"离主体多少字符以内"——按距离判会漏掉
    「……跑一遍绑定、索引、检索、URL 校验、记忆升降和审计，共 54 项」这类长句，
    而漏掉的那一处正是最容易忘记同步的那一处。
    """
    actual: dict[str, int] = {}

    state = _run([sys.executable, "scripts/test_state.py"], root)
    match = re.search(r"状态层测试：(\d+) 项", state.stdout or "")
    if res.check(bool(match), f"跑不出状态层项数：{(state.stdout or state.stderr)[-200:]}"):
        actual["state"] = int(match.group(1))
        res.check(state.returncode == 0, f"状态层测试自身未通过（退出码 {state.returncode}）")
        res.note(f"状态层实际 {actual['state']} 项")

    evals_spec = root / "evals" / "blueink-suite.eval.md"
    spec_text = evals_spec.read_text(encoding="utf-8") if evals_spec.is_file() else ""
    block = re.search(r"```json\n(.*?)\n```", spec_text, re.S)
    if res.check(bool(block), "评测规格里找不到 ```json 块，数不出夹具项数"):
        try:
            spec = json.loads(block.group(1))
        except json.JSONDecodeError as exc:
            res.check(False, f"评测规格 JSON 解析失败：{exc}")
            spec = {}
        criteria = [c for c in spec.get("criteria", []) if c.get("type") == "command"]
        golden = spec.get("golden", [])
        if criteria and golden:
            actual["fixture"] = len(criteria) * len(golden)
            res.note(
                f"审计夹具应为 {len(golden)} 个夹具 × {len(criteria)} 项命令型检查 "
                f"= {actual['fixture']} 项"
            )

    labels = {"state": "状态层", "fixture": "审计夹具"}
    for path, text in docs.items():
        rel = path.relative_to(root).as_posix()
        if path.name in COUNT_EXEMPT_DOCS:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            for subject, value in _attributed_counts(line):
                if subject not in actual:
                    continue
                res.check(
                    value == actual[subject],
                    f"{rel}:{number} 写的{labels[subject]}是 {value} 项，"
                    f"实际 {actual[subject]} 项",
                )
    res.checked += 1


def _subcommands_in_code(root: Path) -> list[str]:
    tree = ast.parse((root / "scripts" / "blueink.py").read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_parser"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            names.append(str(node.args[0].value))
    return names


def _claim_subcommands(root: Path, docs: dict[Path, str], res: Result) -> None:
    """子命令面**双向**核对，并挡住越界能力。

    三条都必须成立：代码里的子命令与入口 docstring 那份清单一致（少写等于藏了能力，
    多写等于承诺了不存在的命令）；每个子命令至少在一份文档里出现过（未文档化的命令
    没人知道该不该用）；越界名单里的命令一个都不许出现。
    """
    code = _subcommands_in_code(root)
    entry = (root / "scripts" / "blueink.py").read_text(encoding="utf-8")
    block = re.search(r"子命令：\n\n(.*?)\n\n", entry, re.S)
    documented = (
        {line.split()[0] for line in block.group(1).splitlines() if line.strip()}
        if block else set()
    )
    # check-verdict 是给评测规格用的自检入口，不进面向老师的子命令清单
    internal = {"check-verdict"}
    res.check(bool(block), "blueink.py 的 docstring 里找不到子命令清单")
    extra_in_code = sorted(set(code) - internal - documented)
    extra_in_docs = sorted(documented - (set(code) - internal))
    res.check(
        set(code) - internal == documented,
        f"子命令表与 docstring 不一致：只在代码里 {extra_in_code}，"
        f"只在文档里 {extra_in_docs}"
        # 两边都空却仍然不相等，只有一种成因：某个内部命令被写进了面向老师的清单。
        # 不点出来的话这条报错会指着两个空列表，而那等于没有定位。
        + (f"（内部命令 {sorted(internal & documented)} 不该进这份清单）"
           if not extra_in_code and not extra_in_docs else ""),
    )

    all_docs = "\n".join(docs.values())
    for name in code:
        res.check(
            name in all_docs,
            f"子命令 {name} 没有出现在任何文档里——未文档化的能力等于没人知道边界",
        )
    for bad in sorted(FORBIDDEN_SUBCOMMANDS & set(code)):
        res.check(
            False,
            f"子命令 {bad} 越界：方法论第八节声明不承担改稿编排与版本回退，"
            f"给出阶段回退命令等于把这条边界作废",
        )
    res.checked += 1


def _claim_boundaries(root: Path, docs: dict[Path, str], res: Result) -> None:
    """边界清单必须同时出现在《方法论内核》与设计说明，一处不缺。"""
    core = docs.get(root / "references" / "methodology-core.md", "")
    design = docs.get(root / "DESIGN_NOTES.md", "")
    skill = docs.get(root / "SKILL.md", "")
    res.check(
        bool(core and design and skill),
        "读不到 methodology-core.md / DESIGN_NOTES.md / SKILL.md",
    )
    for word in BOUNDARY_KEYWORDS:
        res.check(word in core, f"《方法论内核》边界节缺「{word}」")
        res.check(word in design, f"DESIGN_NOTES.md 边界节缺「{word}」")
    res.check(
        "能力边界" in skill and "文案生成" in skill,
        "SKILL.md 缺「能力边界：文案生成」——这是范围边界的唯一显式声明点",
    )


def _claim_python_floor(root: Path, docs: dict[Path, str], res: Result) -> None:
    """README 声明的 Python 底线必须等于 --compat 实际执行的底线。"""
    floor_text = ".".join(str(n) for n in FLOOR)
    readme = docs.get(root / "README.md", "")
    match = re.search(r"需要 Python (\d+\.\d+) 或更高", readme)
    res.check(bool(match), "README 里找不到「需要 Python x.y 或更高」这句声明")
    if match:
        res.check(
            match.group(1) == floor_text,
            f"README 声明底线 {match.group(1)}，self_check.py 守的是 {floor_text}",
        )
    req = root / "requirements.txt"
    res.check(req.is_file(), "缺 requirements.txt——零依赖也要显式声明，否则它只是一句口头承诺")
    if req.is_file():
        text = req.read_text(encoding="utf-8")
        res.check(
            floor_text in text,
            f"requirements.txt 里没写明底线 {floor_text}",
        )
        res.check(
            not [
                line for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ],
            "requirements.txt 出现了非注释条目，但技能声明的是零依赖——两者必须同时改",
        )


def _claim_retention(root: Path, docs: dict[Path, str], res: Result) -> None:
    """留存策略的默认值必须与文档一致。"""
    sys.path.insert(0, str(root / "scripts"))
    try:
        import run_record  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        res.check(False, f"导入 run_record 失败：{exc}")
        return
    all_docs = "\n".join(docs.values())
    res.check(
        f"保留 {run_record.KEEP_DAYS} 天" in all_docs or f"{run_record.KEEP_DAYS} 天" in all_docs,
        f"没有任何文档写明留存期是 {run_record.KEEP_DAYS} 天——留存期不写出来等于承诺永久保存",
    )
    res.check(
        f"最近 {run_record.KEEP_RUNS} 次" in all_docs,
        f"没有任何文档写明至少保留最近 {run_record.KEEP_RUNS} 次运行",
    )


def _claim_script_inventory(root: Path, _docs: dict[Path, str], res: Result) -> None:
    """脚本清单双向核对：没有未登记的脚本，也没有登记了却不存在的脚本。

    这道检查同时是**规模控制**的机械化。复杂度失控不是一次大改动，是一次加一个
    脚本；每个新脚本都必须进入自研或外来件白名单，才加得进来。
    """
    actual = {p.name for p in script_files(root)}
    registered = OWN_SCRIPTS | UPSTREAM_SCRIPTS
    res.check(
        actual == registered,
        f"脚本登记不一致：未登记 {sorted(actual - registered)}，"
        f"登记但不存在 {sorted(registered - actual)}",
    )


def _claim_contract_names(root: Path, docs: dict[Path, str], res: Result) -> None:
    """五项契约的名称与顺序：审计器代码与评测规格必须一致。"""
    audit_src = (root / "scripts" / "audit.py").read_text(encoding="utf-8")
    found = re.findall(r'Check\("A(\d)", "([^"]+)"\)', audit_src)
    res.check(
        [name for _, name in sorted(found)] == CONTRACT_NAMES,
        f"审计器契约名与预期不符：{[n for _, n in sorted(found)]} != {CONTRACT_NAMES}",
    )
    spec = docs.get(root / "evals" / "blueink-suite.eval.md", "")
    for name in CONTRACT_NAMES:
        res.check(name in spec, f"评测规格里缺契约名「{name}」")


def _report_composition(root: Path, res: Result) -> None:
    """报告规模构成。**只报告不断言**——行数会随每次编辑变动，把它写成硬阈值只会
    制造一道谁都学会绕过的门。构成本身才是要点：多少是方法论、多少是自研状态层、
    多少是可与上游重新同步的外来件、多少是审计夹具。"""
    def total(paths: list[Path]) -> tuple[int, int]:
        return len(paths), sum(
            len(p.read_text(encoding="utf-8", errors="replace").splitlines()) for p in paths
        )

    scripts = script_files(root)
    own = [p for p in scripts if p.name not in UPSTREAM_SCRIPTS]
    upstream = [p for p in scripts if p.name in UPSTREAM_SCRIPTS]
    fixtures = sorted((root / "evals" / "golden").rglob("*"))
    fixtures = [p for p in fixtures if p.is_file()]
    prose = [p for p in doc_files(root) if p.parent.name != "golden"]

    for label, group in (
        ("方法论与文档", prose), ("自研脚本", own),
        ("上游同步件", upstream), ("审计夹具", fixtures),
    ):
        count, lines = total(group)
        res.note(f"规模构成 · {label}：{count} 个文件 / {lines} 行")


# --- 门三：变异承重 ---------------------------------------------------------

# (id, 目标文件, 原文, 变异后, 必须转红的检查, 这个变异刻画什么失败形态)
MUTATIONS: list[tuple[str, str, str, str, list[str], str]] = [
    (
        "index-hash",
        "scripts/index_kb.py",
        'if old.get("hash") == content_hash(path):',
        'if old.get("size") == path.stat().st_size and old.get("mtime") == '
        'datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"):',
        ["scripts/test_state.py"],
        "增量索引改用 size+mtime 判断复用：同长度改写并恢复修改时间后静默漏更新，"
        "检索永远拿不到新内容且不报错",
    ),
    (
        "instruction-artifact",
        "scripts/index_kb.py",
        "def _is_instruction_artifact(rel: str, roots: list[Path]) -> bool:\n"
        '    """这个文件是否属于某个历史技能包。"""',
        "def _is_instruction_artifact(rel: str, roots: list[Path]) -> bool:\n"
        '    """这个文件是否属于某个历史技能包。"""\n'
        "    return rel.rsplit('/', 1)[-1].lower() in SKILL_ROOT_MARKERS",
        ["scripts/test_state.py"],
        "混进来的技能包只隔离入口文件而不是整棵子树：其 references/ 里的固定模板"
        "重新进入检索，让外部提示词接管本次判断",
    ),
    (
        "confidence-cap",
        "scripts/memory.py",
        "CONFIDENCE_MAX = 0.9",
        "CONFIDENCE_MAX = 1.0",
        ["scripts/test_state.py"],
        "置信度可以饱和到 1.0：再来一个反例也无法降低它，条件化记忆退化成硬规则",
    ),
    (
        "url-whitelist",
        "scripts/official.py",
        'matched = next((d for d in whitelist if host == d or host.endswith(f".{d}")), None)',
        "matched = next((d for d in whitelist if d in host), None)",
        ["scripts/test_state.py"],
        "官方来源白名单退化成子串匹配：lixiang.com.evil.cn 这类后缀伪装会被放行",
    ),
    (
        "audit-incomplete",
        "scripts/audit.py",
        'verdict = "incomplete"',
        'verdict = "pass"',
        ["scripts/run_evals.py --rollout --include-holdout"],
        "审计器把「运行没跑完」判成「流程通过」：中断的运行会拿到一张通行证，"
        "而这正是保留测试 case-5 存在的理由",
    ),
    (
        "sufficiency-dimensions",
        "scripts/audit.py",
        "    named = sufficiency_dimensions(stopped)\n"
        '    prefix = "本次零轮访谈" if zero_round else "访谈停止"',
        "    named = [\"事实安全\"] if stopped else []\n"
        '    prefix = "本次零轮访谈" if zero_round else "访谈停止"',
        ["scripts/test_state.py"],
        "停止理由只要非空就算指名了维度：一句「信息已经足够」照样过审，"
        "而那句话对任何一次访谈都成立，动态充分性就此失去机械约束",
    ),
    (
        "duplicate-question",
        "scripts/audit.py",
        '    stripped = re.sub(r"[\\s，。、；：？！?!,.;:（）()「」『』\\"\'“”‘’—\\-]+", "", str(text or ""))\n'
        "    return stripped.lower()",
        '    stripped = re.sub(r"[\\s，。、；：？！?!,.;:（）()「」『』\\"\'“”‘’—\\-]+", "", str(text or ""))\n'
        "    return stripped.lower() + str(id(text))",
        ["scripts/test_state.py"],
        "问题归一化带上对象身份：同一个问题问两遍不再算重复，"
        "老师会开始敷衍，而敷衍的回答比没有回答更危险",
    ),
]


def gate_mutation(root: Path, only: str | None = None) -> Result:
    res = Result("mutation")
    for mid, target, before, after, checks, symptom in MUTATIONS:
        if only and only != mid:
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"blueink-mut-{mid}-"))
        try:
            copy = tmp / "skill"
            shutil.copytree(
                root, copy,
                ignore=shutil.ignore_patterns("__pycache__", ".git", ".blueink"),
            )
            path = copy / target
            source = path.read_text(encoding="utf-8")
            # 靶点找不到 = 变异已过期。这必须是失败而不是跳过：一个打不进去的变异
            # 什么都没测到，却会让这道门继续显示绿色。
            if not res.check(
                source.count(before) == 1,
                f"[{mid}] 变异靶点在 {target} 里出现 {source.count(before)} 次（应为 1 次），"
                f"变异已过期，这道门实际什么都没测",
            ):
                continue
            path.write_text(source.replace(before, after), encoding="utf-8")

            for check_cmd in checks:
                cmd = [sys.executable, *check_cmd.split()]
                proc = _run(cmd, copy, nested=True)
                res.check(
                    proc.returncode != 0,
                    f"[{mid}] 注入「{symptom}」后 {check_cmd} 仍然通过——这道检查抓不住它",
                )
                if proc.returncode != 0:
                    res.note(f"[{mid}] {check_cmd} 已转红（退出码 {proc.returncode}）")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return res


# --- 入口 -------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    # 嵌套调用直接退出。走到这里说明本脚本是被自己发起的那条链路又叫了一次
    # （评测规格里的 self-* 检查 → 本脚本 → 变异门 → 评测 harness → 又一次 self-*）。
    # 不剪断这条环，第一次 evolve.py 就会无限递归，而症状是卡住不是报错。
    if os.environ.get(NESTED_FLAG):
        print("self_check: 检测到嵌套调用，跳过（环已在最外层跑过一次）")
        return 0

    parser = argparse.ArgumentParser(description="自证门：把技能对外的声明变成机械检查。")
    parser.add_argument("--compat", action="store_true", help="只跑版本底线")
    parser.add_argument("--claims", action="store_true", help="只跑声明一致性")
    parser.add_argument("--mutation", action="store_true", help="只跑变异承重")
    parser.add_argument("--only", help="只跑某一个变异（配合 --mutation）")
    parser.add_argument("--json", action="store_true", help="机器可读输出")
    parser.add_argument("--root", default=None, help="技能根目录，默认脚本上一级")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    selected = [
        name for name, flag in
        (("compat", args.compat), ("claims", args.claims), ("mutation", args.mutation))
        if flag
    ] or ["compat", "claims", "mutation"]

    results: list[Result] = []
    if "compat" in selected:
        results.append(gate_compat(root))
    if "claims" in selected:
        results.append(gate_claims(root))
    if "mutation" in selected:
        results.append(gate_mutation(root, args.only))

    failed = sum(len(r.failures) for r in results)
    payload = {
        "root": str(root),
        "status": "FAIL" if failed else ("PASS-WITH-SKIPS" if any(r.skipped for r in results) else "PASS"),
        "gates": [r.as_dict() for r in results],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"自证门：{root}")
        for r in results:
            print(f"\n== {r.gate}（{r.checked} 项）==")
            for note in r.notes:
                print(f"  · {note}")
            for skipped in r.skipped:
                print(f"  [跳过] {skipped}")
            for fail in r.failures:
                print(f"  [失败] {fail}")
            if not r.failures:
                print("  通过")
        print(f"\n状态：{payload['status']}")
        if payload["status"] == "PASS-WITH-SKIPS":
            print("有检查被跳过，上面已写明跳过的是什么、代价是什么——不要把它读成全绿。")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
