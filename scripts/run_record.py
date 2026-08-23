#!/usr/bin/env python3
"""运行记录：让"这次到底有没有走技能、走到了哪一步"变成可查的事实。

最常见的失败是静默的——模型没有真的执行技能，但输出看起来像是执行了。
所以每次运行都开一个目录，落 ``meta.json``，并回显一行启动回执。业务侧永远看
不到这些文件，它们只在定位问题时被读取。

**这套记账有成本，所以它自带留存期。** 运行目录里躺着访谈原文、素材路径和交付
正文——它是可定位性的载体，也是一份会无限增长的客户内容留存。``purge`` 把"留多
久"变成一条显式策略而不是"永远不删"：默认保留 90 天，且无论多旧至少保留最近 20
次（定位问题通常只需要最近几次，而"刚出问题就被清掉"比留久一点更糟）。
默认试运行，加 ``--apply`` 才真的删。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import workspace

ENTRY = "/blueink-suite"
MODES = ("生成", "绑定", "学习", "定位")

# 本次证据边界。``attachments`` 表示老师说了"以附件为准"，先只用附件成稿；
# ``kb`` 是默认，允许在绑定库内自主检索。它是一条**声明**而不是一道锁——
# 真的出现会阻止成稿的高影响缺口时照样可以检索，只要在证据回执的 gaps 里写清
# 缺什么。声明的作用是让审计能发现"没有缺口却展开了整库普查"这种静默浪费。
EVIDENCE_BOUNDARIES = ("kb", "attachments")

# 留存策略的默认值。写成常量而不是散在文档里，是因为"文档说 90 天、代码写 365 天"
# 这类漂移正是本技能要消灭的东西——self_check.py --claims 会核对两处是否一致。
KEEP_DAYS = 90
KEEP_RUNS = 20

STAGE_NAMES = {
    0: "运行开启",
    1: "逐轮访谈",
    2: "取证",
    3: "编辑策略",
    4: "写作程序",
    5: "成稿",
    6: "来源核验",
    7: "编辑反方",
    8: "交付",
    9: "运行归档",
    10: "反馈归因",
    11: "记忆晋级",
}

# 各阶段的产出文件，audit 与 doctor 用它判断运行走到哪、缺了什么
STAGE_ARTIFACTS = {
    1: ["interview.json"],
    2: ["evidence.json"],
    3: ["strategy.json"],
    4: ["program.json"],
    5: ["draft.md", "draft-a.md", "write-receipt.json"],
    6: ["verify.json", "verify-a.json"],
    7: ["adversary.json"],
    8: ["delivery.md"],
    10: ["feedback.json"],
}

_SLUG = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-") or "run"


def new_run_id(brand_key: str, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_slug(brand_key)}"


def _unique_run_id(base: str, runs_root: Path) -> str:
    """同一秒内开两次运行不能共用目录，否则第一次的回执会被第二次覆盖。"""
    if not (runs_root / base / "meta.json").is_file():
        return base
    n = 2
    while (runs_root / f"{base}-{n}" / "meta.json").is_file():
        n += 1
    return f"{base}-{n}"


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def register_attachments(paths: list[str], brand: str = "") -> list[dict[str, Any]]:
    """把老师本次显式提供的附件登记成本次运行的证据。

    这是"证据边界"这条方法论原则的唯一执行点。老师给的文件不受绑定根限制——
    它由老师显式指定，来源强度高于任何检索结果；绑定根约束的是**系统自主检索
    的范围**。但登记是硬要求：没有登记，审计就无法区分"老师给的"与"自己越界
    读的"，于是要么放过真正的越界，要么把老师的授权判成违约。两种都不能接受。

    记内容哈希是因为附件多半在知识库之外，事后可能被改动或移走；哈希让"当时读
    到的是哪一版"仍然可查。
    """
    registered: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in paths:
        # 一律 resolve：绝对路径也要。macOS 上 /tmp 是 /private/tmp 的软链接，
        # 不归一化会让 meta.json 里记的路径与审计比较时算出来的不是同一个字符串，
        # 于是"老师明确给的文件"被判成越界——正是这一版要修的那个矛盾。
        try:
            resolved = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise workspace.WorkspaceError(f"附件路径无法解析：{raw}（{exc}）") from exc
        if not resolved.is_file():
            raise workspace.WorkspaceError(
                f"附件不存在或不是文件：{raw}\n"
                f"（附件要给绝对路径；登记的是老师本次实际提供的文件，不是目录）"
            )
        # 归一化之后去重。同一份文件被写成两种路径传进来是常态（老师粘一次、
        # 主智能体又从别处补一次），登记成两份会让"本次附件 N 份"这个数字骗人。
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        registered.append({
            "path": key,
            "sha256": _sha256(resolved),
            "bytes": resolved.stat().st_size,
            "brand": brand,
            "source": "user",
        })
    return registered


def open_run(
    mode: str = "生成",
    start=None,
    when: datetime | None = None,
    attachments: list[str] | None = None,
    evidence_boundary: str | None = None,
    brand: str | None = None,
) -> dict[str, Any]:
    """开一次运行，返回 meta（含 run_id 与要回显的启动回执行）。

    Args:
        brand: 本次要写的品牌。给了就与工作空间绑定的品牌核对，不一致直接拒绝——
            这是"品牌与知识库不匹配"这类静默失败的唯一拦截点。

    两种情况允许在未绑定工作空间的项目里开启运行：

    - ``绑定`` 模式：这一次运行的任务就是完成绑定。
    - **老师本次显式给了附件**：此时证据边界就是这几份文件，不需要品牌知识库。
      老师说"参考这两个文件写一篇"是完全正常的请求，为它强制先做一次绑定，等于
      为一个五分钟的任务索要一次知识工程。

    除此之外一律拒绝：否则会开出一次"品牌: 未绑定"的运行，启动回执照样打出来，
    看起来像正常执行——而那正是最难查的一类失败。
    """
    if mode not in MODES:
        raise ValueError(f"mode 必须是 {MODES} 之一，收到 {mode!r}")
    if evidence_boundary is not None and evidence_boundary not in EVIDENCE_BOUNDARIES:
        raise ValueError(
            f"evidence_boundary 必须是 {EVIDENCE_BOUNDARIES} 之一，收到 {evidence_boundary!r}"
        )

    bound = workspace.is_bound(start)
    attach_only = bool(attachments) and not bound
    if not bound and mode != "绑定" and not attach_only:
        raise workspace.WorkspaceError(
            f"当前项目未绑定品牌知识库，且本次没有给任何附件，无法以「{mode}」模式启动。\n"
            f"两条出路，选一条：\n"
            f"  1. 绑定这个品牌的知识库："
            f"blueink.py bind --brand <品牌> --teacher <文案老师> --kb <知识库目录>"
            f"（还没有目录时加 --create 让它建出来）\n"
            f"  2. 本次只参考指定文件，不用知识库：open --mode {mode} --attach <文件绝对路径>"
        )
    if mode == "绑定" and attachments:
        # 绑定模式没有本次任务，附件无处可用；而它的品牌归属只能记成"未绑定"，
        # 那是一条毫无意义又看起来正常的记录。宁可在这里拒绝。
        raise workspace.WorkspaceError(
            "绑定模式不接受 --attach：这一次运行没有本次任务，附件的品牌归属只能记成"
            "「未绑定」。先完成 bind，再以「生成」或「学习」模式开启带附件的运行。"
        )

    brand_name = brand_key = "未绑定"
    teacher = "未记名"
    kb = ""
    if bound:
        ws = workspace.load(start)
        # 品牌核对发生在开启运行之前，而不是等到取证时才发现检索命中的全是别家品牌。
        # 判定逻辑在 workspace.brand_matches，这里只负责拒绝和给出路。
        if brand:
            matched, why = workspace.brand_matches(ws, brand)
            if not matched:
                raise workspace.WorkspaceError(
                    f"{why}。用当前知识库给另一个品牌写稿，会把别家客户的表达和事实"
                    f"带进这一稿，而这类错误在成稿里看不出来。\n"
                    f"三条出路，选一条：\n"
                    f"  1. 这一稿确实是「{ws.get('brand')}」的，本次品牌名写错了——改过来重开。\n"
                    f"  2. 要写「{brand}」，就换到那个品牌的项目目录去执行；"
                    f"没有的话新建一个目录再 bind（加 --create 可以顺带建出知识库骨架）。\n"
                    f"  3. 只想参考几份指定文件、不用知识库："
                    f"在一个没绑定的目录里 open --attach <文件绝对路径>。\n"
                    f"确实要在这个项目里改绑，用 bind --force —— 它会作废现有索引，"
                    f"并让已有记忆的归属不再准确。"
                )
        brand_name, brand_key = str(ws["brand"]), str(ws["brand_key"])
        teacher = str(ws.get("teacher") or "未记名")
        kb = str(ws["kb_root"])
    elif attach_only and brand:
        # 没有知识库时，本次品牌只是一个标签：没有任何绑定可以与它冲突。
        # 照记下来，因为交付和审计都要知道这一稿写的是谁。
        brand_name = brand.strip()
        brand_key = workspace.derive_brand_key(brand)

    task_attachments = register_attachments(attachments or [], brand=brand_name)
    # 给了附件而没显式声明边界时，默认就是"以附件为准"：老师附了文件却仍然被展开成
    # 整库普查，代价全部由他承担。未绑定时更是唯一可能的边界。
    if evidence_boundary is None:
        evidence_boundary = "attachments" if task_attachments else "kb"
    if attach_only and evidence_boundary != "attachments":
        raise workspace.WorkspaceError(
            "未绑定知识库时证据边界只能是 attachments：没有 kb_root，"
            "声明 kb 会让下游以为有一个可检索的品牌库。"
        )

    run_id = _unique_run_id(new_run_id(brand_key, when), workspace.runs_dir(start))
    run_dir = workspace.runs_dir(start) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # 启动回执要让"这次到底有没有知识库参与"一眼可见。未绑定却照打一行正常回执，
    # 老师会以为品牌库已经生效，而实际上取证只看了那几份附件。
    scope = f"品牌: {brand_name}" if bound else f"品牌: {brand_name} · 无知识库·以附件为准"
    meta = {
        "run_id": run_id,
        "started_via": ENTRY,
        "started_at": (when or datetime.now()).isoformat(timespec="seconds"),
        "mode": mode,
        "brand": brand_name,
        "brand_key": brand_key,
        "brand_asked": (brand or "").strip(),
        "teacher": teacher,
        "kb_root": kb,
        "bound": bound,
        "task_attachments": task_attachments,
        "evidence_boundary": evidence_boundary,
        "stage": 0,
        "stage_name": STAGE_NAMES[0],
        "closed_at": None,
        # 回执里带老师，是因为"记忆归属错了"这类问题只有在这里能被一眼看到：
        # 老师看见的名字不是自己，就说明这个项目被别人绑过。
        "launch_receipt": (
            f"BlueInk 已启动 · run-id: {run_id} · {scope} · 老师: {teacher} · 模式: {mode}"
        ),
    }
    _write_meta(run_dir, meta)
    return meta


def attachment_paths(meta: Any) -> list[str]:
    """meta 里登记过的附件绝对路径。审计与检索都从这里取，不各自解析一遍。"""
    if not isinstance(meta, dict):
        return []
    items = meta.get("task_attachments")
    if not isinstance(items, list):
        # 不是列表就当没有。这里刻意不去"尽力解析"：一个畸形的 task_attachments
        # 如果被当成路径集合用，会让审计放过它算出来的每一个"附件"。
        return []
    out: list[str] = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("path"), str):
            out.append(item["path"])
        elif isinstance(item, str):
            out.append(item)
    return out


def _meta_path(run_dir: Path) -> Path:
    return run_dir / "meta.json"


def _write_meta(run_dir: Path, meta: dict[str, Any]) -> None:
    _meta_path(run_dir).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_dir_for(run_id: str, start=None) -> Path:
    return workspace.runs_dir(start) / run_id


def load_meta(run_id: str, start=None) -> dict[str, Any]:
    path = _meta_path(run_dir_for(run_id, start))
    if not path.is_file():
        raise FileNotFoundError(f"找不到运行记录 {run_id}（缺 {path}）")
    return json.loads(path.read_text(encoding="utf-8"))


def set_stage(run_id: str, stage: int, start=None) -> dict[str, Any]:
    meta = load_meta(run_id, start)
    meta["stage"] = stage
    meta["stage_name"] = STAGE_NAMES.get(stage, str(stage))
    _write_meta(run_dir_for(run_id, start), meta)
    return meta


def close_run(run_id: str, start=None) -> dict[str, Any]:
    """归档一次运行，并把摘要写进 runs/index.json。"""
    run_dir = run_dir_for(run_id, start)
    meta = load_meta(run_id, start)
    meta["closed_at"] = datetime.now().isoformat(timespec="seconds")
    meta["artifacts"] = sorted(p.name for p in run_dir.iterdir() if p.is_file())
    _write_meta(run_dir, meta)

    summary_path = workspace.runs_dir(start) / "index.json"
    entries: list[Any] = []
    if summary_path.is_file():
        try:
            entries = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            entries = []
    if not isinstance(entries, list):
        entries = []
    entries = [e for e in entries if not (isinstance(e, dict) and e.get("run_id") == run_id)]
    entries.append(
        {
            "run_id": run_id,
            "brand": meta.get("brand"),
            "mode": meta.get("mode"),
            "started_at": meta.get("started_at"),
            "closed_at": meta.get("closed_at"),
            "stage": meta.get("stage"),
            "stage_name": meta.get("stage_name"),
            "artifacts": len(meta.get("artifacts") or []),
        }
    )
    entries.sort(key=lambda e: str(e.get("started_at") or ""))
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def latest(start=None) -> dict[str, Any] | None:
    """最近一次运行的 meta；没有运行返回 None。"""
    root = workspace.runs_dir(start)
    if not root.is_dir():
        return None
    candidates = sorted(
        (p for p in root.iterdir() if p.is_dir() and _meta_path(p).is_file()),
        key=lambda p: p.name,
    )
    if not candidates:
        return None
    try:
        return json.loads(_meta_path(candidates[-1]).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def present_artifacts(run_dir: Path) -> set[str]:
    return {p.name for p in run_dir.iterdir() if p.is_file()} if run_dir.is_dir() else set()


def reached_stage(run_dir: Path) -> int:
    """按产出文件判断这次运行实际走到了哪一步。"""
    present = present_artifacts(run_dir)
    reached = 0
    for stage in sorted(STAGE_ARTIFACTS):
        if any(name in present for name in STAGE_ARTIFACTS[stage]):
            reached = stage
    return reached


def _started_at(run_dir: Path) -> datetime | None:
    """这次运行的开始时间。以 meta.json 里记的为准，读不到才退回目录 mtime。

    不用 mtime 当主判据：``audit`` 会重读运行目录，某些编辑器也会碰时间戳，按
    mtime 清理等于按"最近谁看过"清理，而不是按"这次运行有多旧"清理。
    """
    try:
        raw = json.loads(_meta_path(run_dir).read_text(encoding="utf-8")).get("started_at")
        if raw:
            return datetime.fromisoformat(str(raw))
    except (json.JSONDecodeError, OSError, ValueError, AttributeError):
        pass
    try:
        return datetime.fromtimestamp(run_dir.stat().st_mtime)
    except OSError:
        return None


def purge(
    keep_days: int = KEEP_DAYS,
    keep_runs: int = KEEP_RUNS,
    apply: bool = False,
    start=None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """按留存策略清理旧运行目录。默认只报告不删除。

    两条保护同时生效，取并集保留：**时间**（``keep_days`` 天内的一律保留）与
    **次数**（最近 ``keep_runs`` 次一律保留，无论多旧）。只有同时越过两条线的
    运行才会被删。次数这条是必要的：一个几个月才写一稿的老师，纯按时间清会把他
    仅有的几次运行全清掉，而那正是他定位问题时唯一的依据。

    ``runs/index.json`` 里对应的摘要条目同步移除，否则会留下指向已删目录的悬空
    记录——``doctor`` 会照着它去找文件，然后报一个不存在的问题。
    """
    root = workspace.runs_dir(start)
    result: dict[str, Any] = {
        "keep_days": keep_days, "keep_runs": keep_runs,
        "applied": apply, "purged": [], "kept": 0,
    }
    if not root.is_dir():
        return result

    runs = sorted(
        (p for p in root.iterdir() if p.is_dir() and _meta_path(p).is_file()),
        key=lambda p: (_started_at(p) or datetime.min, p.name),
    )
    protected = {p.name for p in runs[-keep_runs:]} if keep_runs > 0 else set()
    cutoff = (now or datetime.now()) - timedelta(days=keep_days)

    removed: list[str] = []
    for run_dir in runs:
        started = _started_at(run_dir)
        if run_dir.name in protected or started is None or started >= cutoff:
            result["kept"] += 1
            continue
        result["purged"].append({
            "run_id": run_dir.name,
            "started_at": started.isoformat(timespec="seconds"),
            "reason": f"早于 {cutoff.date()}，且不在最近 {keep_runs} 次内",
        })
        removed.append(run_dir.name)
        if apply:
            shutil.rmtree(run_dir, ignore_errors=True)

    if apply and removed:
        summary_path = root / "index.json"
        if summary_path.is_file():
            try:
                entries = json.loads(summary_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                entries = []
            if isinstance(entries, list):
                entries = [
                    e for e in entries
                    if not (isinstance(e, dict) and e.get("run_id") in set(removed))
                ]
                summary_path.write_text(
                    json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
                )
    return result
