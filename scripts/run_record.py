#!/usr/bin/env python3
"""运行记录：让"这次走到了哪一步"变成可查的事实。

每次新运行都开一个目录并落 ``run.json``。它同时保存登记信息、逐轮回答、已确认
方向与实际阶段时间。明确附件的普通生成先保存轻量方向，再交付唯一可修改正文；只有进入
扩展研究时才补事实原子和完整编辑决策。旧运行的 ``meta.json`` 继续只读兼容。

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
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import miniyaml
import workspace

ENTRY = "/blueink-suite"
ENTRY_ALIASES = (ENTRY, "/blueink-suite:blueink-suite")
MODES = ("生成", "绑定", "学习", "定位")
CURRENT_SCHEMA_VERSION = 6
POLICY_PATH = Path(__file__).resolve().parent.parent / "policies" / "common-policy.yaml"

# 本次证据边界。``attachments`` 表示老师说了"以附件为准"，先只用附件成稿；
# ``kb`` 是默认，允许在绑定库内自主检索。它是一条**声明**而不是一道锁——
# 真的出现会阻止成稿的高影响缺口时照样可以检索，只要在证据回执的 gaps 里写清
# 缺什么。声明的作用是让审计能发现"没有缺口却展开了整库普查"这种静默浪费。
EVIDENCE_BOUNDARIES = ("kb", "attachments")

# 留存策略的默认值。写成常量而不是散在文档里，是因为"文档说 90 天、代码写 365 天"
# 这类漂移正是本技能要消灭的东西——self_check.py --claims 会核对两处是否一致。
KEEP_DAYS = 90
KEEP_RUNS = 20
RUN_META_FIELDS = (
    "schema_version", "run_id", "started_via", "started_at", "mode", "brand", "brand_key",
    "brand_asked", "kb_root", "bound", "task_attachments", "evidence_boundary",
    "policy_version", "policy_check", "interview", "direction", "facts", "decision", "metrics",
    "stage", "stage_name", "closed_at", "artifacts",
)

STAGE_NAMES = {
    0: "运行开启",
    1: "逐轮访谈",
    2: "取证",
    3: "编辑策略",
    4: "方向已确认",
    5: "可修改稿件已交付",
    6: "高风险句复核",
    7: "条件编辑风险",
    8: "交付核对卡已生成",
    9: "运行归档",
    10: "反馈归因",
    11: "记忆晋级",
}

# 各阶段的产出文件，audit 与 doctor 用它判断运行走到哪、缺了什么
STAGE_ARTIFACTS = {
    5: ["delivery.md"],
    6: ["verify.json"],
    8: ["delivery-check.md"],
    10: ["feedback.json"],
}

SCHEMA_4_STAGE_ARTIFACTS = {
    5: ["draft.md", "draft-a.md"],
    6: ["verify.json", "verify-a.json"],
    8: ["delivery.md"],
    10: ["feedback.json"],
}

SCHEMA_5_STAGE_ARTIFACTS = STAGE_ARTIFACTS

STRONG_WORDS = ("唯一", "全部", "普遍", "均", "最高")
VERDICTS = ("可进入人工初审", "有待确认项", "暂不建议提交")
JUDGEMENTS = ("matched", "drifted", "unsourced", "stale")

RISK_COMPARISONS = STRONG_WORDS + (
    "第一", "领先", "最大", "最低", "最健康", "远高于", "远低于",
    "首个", "首款", "顶级", "标杆", "碾压", "吊打", "完胜", "超越同级",
)
RISK_RELATIONS = ("同比", "环比", "超过", "不足", "增长", "下降", "倍")
RISK_CAUSAL = ("因此", "证明", "说明", "导致", "意味着", "得益于")
RISK_TIMELINESS = ("截至", "当前", "目前", "今日", "本月", "一季度", "季度末")

_SLUG = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", (text or "").lower()).strip("-") or "run"


def new_run_id(brand_key: str, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{_slug(brand_key)}"


def _unique_run_id(base: str, runs_root: Path) -> str:
    """同一秒内开两次运行不能共用目录，否则第一次的记录会被第二次覆盖。"""
    if not any((runs_root / base / name).is_file() for name in ("run.json", "meta.json")):
        return base
    n = 2
    while any((runs_root / f"{base}-{n}" / name).is_file()
              for name in ("run.json", "meta.json")):
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


def common_policy() -> dict[str, Any]:
    """读取并校验全品牌通用规范。

    Returns:
        含版本、硬规则和中文表达默认值的映射。

    Raises:
        ValueError: 文件不可读、YAML 不合法或必需字段缺失。
    """
    try:
        data = miniyaml.load_file(POLICY_PATH)
    except (OSError, miniyaml.YamlError) as exc:
        raise ValueError(f"通用规范无法读取：{POLICY_PATH}（{exc}）") from exc
    if not isinstance(data, dict):
        raise ValueError("通用规范顶层必须是对象")
    for field in ("version", "owner", "last_reviewed", "scope"):
        if not str(data.get(field) or "").strip():
            raise ValueError(f"通用规范缺 {field}")
    all_ids: set[str] = set()
    for group, fields in (
        ("hard_rules", ("id", "name", "rule", "allow", "on_hit")),
        ("expression_defaults", ("id", "name", "rule")),
    ):
        items = data.get(group)
        if not isinstance(items, list) or not items:
            raise ValueError(f"通用规范的 {group} 必须是非空数组")
        ids: set[str] = set()
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                raise ValueError(f"通用规范 {group}[{index}] 必须是对象")
            for field in fields:
                if not str(item.get(field) or "").strip():
                    raise ValueError(f"通用规范 {group}[{index}] 缺 {field}")
            rid = str(item["id"])
            if rid in ids or rid in all_ids:
                raise ValueError(f"通用规范 {group} 出现重复 id：{rid}")
            ids.add(rid)
            all_ids.add(rid)
    return data


def common_policy_version() -> str:
    """返回当前通用规范版本；规范无效时抛出 ``ValueError``。"""
    return str(common_policy()["version"])


def common_policy_rule_ids() -> tuple[str, ...]:
    """按规范顺序返回全部不可覆盖的硬规则编号。"""
    return tuple(str(item["id"]) for item in common_policy()["hard_rules"])


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
    started_via: str = ENTRY,
    one_off: bool = False,
) -> dict[str, Any]:
    """开一次运行并返回 meta。

    Args:
        brand: 本次要写的品牌。给了就与工作空间绑定的品牌核对，不一致直接拒绝——
            这是"品牌与知识库不匹配"这类静默失败的唯一拦截点。

    两种情况允许在未绑定工作空间的项目里开启运行：

    - ``绑定`` 模式：这一次运行的任务就是完成绑定。
    - **老师明确确认本次只用附件**：调用方传入 ``one_off=True``，此时证据边界
      就是这几份文件，不建立长期品牌知识库。显式确认是为了避免附件让首次资料源
      询问被静默跳过。

    除此之外一律拒绝，避免创建没有可用证据边界的运行记录。
    """
    if mode not in MODES:
        raise ValueError(f"mode 必须是 {MODES} 之一，收到 {mode!r}")
    if started_via not in ENTRY_ALIASES:
        raise ValueError(f"started_via 必须是 {ENTRY_ALIASES} 之一，收到 {started_via!r}")
    if evidence_boundary is not None and evidence_boundary not in EVIDENCE_BOUNDARIES:
        raise ValueError(
            f"evidence_boundary 必须是 {EVIDENCE_BOUNDARIES} 之一，收到 {evidence_boundary!r}"
        )

    bound = workspace.is_bound(start)
    attach_only = bool(attachments) and not bound
    if mode == "绑定" and attachments:
        # 绑定模式没有本次任务，附件无处可用；而它的品牌归属只能记成"未绑定"，
        # 那是一条毫无意义又看起来正常的记录。宁可在这里拒绝。
        raise workspace.WorkspaceError(
            "绑定模式不接受 --attach：这一次运行没有本次任务，附件的品牌归属只能记成"
            "「未绑定」。先完成 bind，再以「生成」或「学习」模式开启带附件的运行。"
        )
    if one_off and not attach_only:
        raise workspace.WorkspaceError(
            "--one-off 只用于未绑定项目里的单次附件任务；已绑定项目或没有附件时不要使用。"
        )
    if not bound and mode != "绑定" and not attach_only:
        raise workspace.WorkspaceError(
            f"当前项目尚未绑定品牌历史稿件根目录，无法以「{mode}」模式启动。\n"
            "请先向老师只问一次：这个品牌所有过往稿件所在的共同根目录绝对路径是什么？\n"
            "拿到路径后运行：blueink.py bind --brand <品牌> --kb <共同根目录>，再运行 index。"
        )
    if attach_only and not one_off:
        raise workspace.WorkspaceError(
            "当前项目尚未绑定品牌历史稿件根目录，不能因为本次带了附件就静默跳过首次询问。\n"
            "请先向老师只问一次：这个品牌所有过往稿件所在的共同根目录绝对路径是什么？\n"
            "拿到路径后运行 bind 与 index；如果老师明确回复「本次只用附件」，"
            "再用 open --one-off --attach <文件绝对路径> 开启本次任务。"
        )

    brand_name = brand_key = "未绑定"
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
                    f"换品牌必须新建项目目录；bind --force 只用于当前品牌迁移"
                    f"知识库路径或确认集合层启发式误判。"
                )
        brand_name, brand_key = str(ws["brand"]), str(ws["brand_key"])
        kb = str(workspace.kb_root(start))
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

    meta = {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "run_id": run_id,
        "started_via": started_via,
        "started_at": (when or datetime.now()).isoformat(timespec="seconds"),
        "mode": mode,
        "brand": brand_name,
        "brand_key": brand_key,
        "brand_asked": (brand or "").strip(),
        "kb_root": kb,
        "bound": bound,
        "task_attachments": task_attachments,
        "evidence_boundary": evidence_boundary,
        "policy_version": common_policy_version(),
        "policy_check": None,
        "interview": None,
        "direction": None,
        "facts": [],
        "decision": None,
        "metrics": {},
        "stage": 0,
        "stage_name": STAGE_NAMES[0],
        "closed_at": None,
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
    current = run_dir / "run.json"
    legacy = run_dir / "meta.json"
    return current if current.is_file() or not legacy.is_file() else legacy


def _clean_meta(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError("运行记录顶层不是对象")
    return {field: data[field] for field in RUN_META_FIELDS if field in data}


def _write_meta(run_dir: Path, meta: dict[str, Any]) -> None:
    _atomic_json(_meta_path(run_dir), _clean_meta(meta))


def _atomic_json(path: Path, data: Any) -> None:
    """先完整写入同目录临时文件，再原子替换正式 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        temp = Path(fh.name)
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    temp.replace(path)


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as fh:
        temp = Path(fh.name)
        fh.write(text)
    temp.replace(path)


def run_dir_for(run_id: str, start=None) -> Path:
    return workspace.runs_dir(start) / run_id


def load_meta(run_id: str, start=None) -> dict[str, Any]:
    path = _meta_path(run_dir_for(run_id, start))
    if not path.is_file():
        raise FileNotFoundError(f"找不到运行记录 {run_id}（缺 {path}）")
    return _clean_meta(json.loads(path.read_text(encoding="utf-8")))


def _allowed_source(path: str, meta: dict[str, Any]) -> bool:
    if path.startswith(("http://", "https://")):
        return True
    kb_root = str(meta.get("kb_root") or "")
    try:
        raw = Path(path).expanduser()
        resolved = ((Path(kb_root) / raw) if kb_root and not raw.is_absolute() else raw).resolve()
    except (OSError, RuntimeError):
        return False
    attachments = {
        str(Path(item).expanduser().resolve())
        for item in attachment_paths(meta)
    }
    if str(resolved) in attachments:
        return True
    return bool(kb_root) and workspace.within(resolved, kb_root)


def _one_question(text: str) -> bool:
    return str(text or "").count("？") + str(text or "").count("?") <= 1


def _elapsed_seconds(started: str | None, ended: datetime) -> float | None:
    """计算两个本地 ISO 时间之间的秒数；历史脏值返回 ``None``。"""
    if not started:
        return None
    try:
        value = (ended - datetime.fromisoformat(str(started))).total_seconds()
    except (TypeError, ValueError):
        return None
    return round(max(0.0, value), 3)


def _draft_sentences(draft: str) -> list[str]:
    """按中文正文标点切成可回指的原句，并保持首次出现顺序。"""
    sentences: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"[^。！？!?\n]+[。！？!?]?", draft):
        quote = match.group(0).strip()
        if quote and quote not in seen:
            seen.add(quote)
            sentences.append(quote)
    return sentences


def draft_risk_sentences(draft: str) -> list[dict[str, Any]]:
    """从正文机械提取需要模型复核的句子，不判断它们是否正确。

    提取只负责缩小核验输入：数字、比较、数量关系、因果和时效仍由当前智能体对照
    本次来源判断。句子保留正文原文，避免模型为核验再次手抄整篇并产生引用偏差。
    """
    risks: list[dict[str, Any]] = []
    for quote in _draft_sentences(draft):
        signals: list[str] = []
        if re.search(r"\d", quote):
            signals.append("数字或日期")
        if any(word in quote for word in RISK_COMPARISONS):
            signals.append("比较范围")
        if any(word in quote for word in RISK_RELATIONS):
            signals.append("数量关系")
        if any(word in quote for word in RISK_CAUSAL):
            signals.append("因果表达")
        if any(word in quote for word in RISK_TIMELINESS):
            signals.append("时效")
        if not signals:
            continue
        risks.append({"id": f"R{len(risks) + 1}", "quote": quote, "signals": signals})
    return risks


def _validate_style_refs(value: Any, meta: dict[str, Any]) -> list[dict[str, str]]:
    """校验本次真正打开的历史稿件；它们只提供风格，不进入事实来源。"""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 3:
        raise ValueError("style_refs 必须是数组且最多 3 条")
    kb_root = str(meta.get("kb_root") or "")
    if value and not kb_root:
        raise ValueError("未绑定品牌知识库时不能登记历史风格参考")
    root = Path(kb_root).expanduser().resolve() if kb_root else None
    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"style_refs[{index}] 必须是对象")
        raw = str(item.get("path") or "").strip()
        why = str(item.get("why") or "").strip()
        if not raw or not why:
            raise ValueError(f"style_refs[{index}] 必须包含 path 和 why")
        try:
            path = Path(raw).expanduser()
            resolved = ((root / path) if root is not None and not path.is_absolute() else path).resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"style_refs[{index}].path 无法解析：{raw}（{exc}）") from exc
        if root is None or not resolved.is_file() or not workspace.within(resolved, root):
            raise ValueError(f"style_refs[{index}].path 不存在或越过绑定知识库：{raw}")
        key = str(resolved)
        if key in seen:
            continue
        seen.add(key)
        caveat = str(item.get("caveat") or "").strip() or "只参考表达，不作为事实来源"
        refs.append({
            "path": resolved.relative_to(root).as_posix(),
            "why": why,
            "caveat": caveat,
        })
    return refs


def _validate_decision(payload: Any, meta: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("decision 输入顶层必须是对象")

    interview = payload.get("interview")
    direction = payload.get("direction")
    facts = payload.get("facts")
    decision = payload.get("decision")
    style_refs = _validate_style_refs(payload.get("style_refs"), meta)
    if not isinstance(interview, dict) or not isinstance(interview.get("rounds"), list):
        raise ValueError("decision.interview.rounds 必须是数组")
    rounds = interview["rounds"]
    if meta.get("mode") == "生成" and not rounds:
        raise ValueError("生成任务不允许零轮访谈：至少要有一次成稿前方向确认")
    ordinary = 0
    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            raise ValueError(f"interview.rounds[{index - 1}] 必须是对象")
        kind = str(item.get("kind") or "")
        if kind not in ("gap", "direction", "hard_conflict"):
            raise ValueError(f"第 {index} 轮 kind 必须是 gap、direction 或 hard_conflict")
        ordinary += kind != "hard_conflict"
        question = str(item.get("question") or "").strip()
        answer = str(item.get("answer") or "").strip()
        if not question or not answer:
            raise ValueError(f"第 {index} 轮必须同时记录 question 和老师原话 answer")
        if not _one_question(question):
            raise ValueError(f"第 {index} 轮一次问了多个问题")
    if ordinary > 2:
        raise ValueError("普通生成最多两轮；额外轮次只能用于事实或来源 hard_conflict")
    if meta.get("mode") == "生成" and str(rounds[-1].get("kind") or "") != "direction":
        raise ValueError("生成任务最后一轮必须是成稿前方向确认")

    if not isinstance(direction, dict) or direction.get("confirmed_by_user") is not True:
        raise ValueError("direction.confirmed_by_user 必须为 true")
    options = direction.get("options")
    if not isinstance(options, list) or not 1 <= len(options) <= 3:
        raise ValueError("direction.options 必须包含 1 到 3 个真实写法选项")
    option_ids = set()
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            raise ValueError(f"direction.options[{index}] 必须是对象")
        for field in ("id", "name", "claim", "suitable_for", "cost"):
            if not str(option.get(field) or "").strip():
                raise ValueError(f"direction.options[{index}] 缺字段 {field}")
        option_ids.add(str(option["id"]))
    if str(direction.get("selected") or "") not in option_ids:
        raise ValueError("direction.selected 必须指向 options 中的 id")
    if not any(str(item.get("kind") or "") == "direction" for item in rounds):
        raise ValueError("interview.rounds 缺成稿前方向确认记录")

    attachment_fast_path = (
        facts is None
        and decision is None
        and meta.get("evidence_boundary") == "attachments"
        and bool(attachment_paths(meta))
    )
    if attachment_fast_path:
        facts = []
        decision = {"path": "attachment-delivery-first"}
        if style_refs:
            decision["style_refs"] = style_refs
    elif not isinstance(facts, list) or len(facts) > 12:
        raise ValueError("facts 必须是数组且最多 12 条")
    if meta.get("mode") == "生成" and not facts and not attachment_fast_path:
        raise ValueError("生成任务至少需要一条可追溯事实原子")
    fact_ids = set()
    for index, fact in enumerate(facts):
        if not isinstance(fact, dict):
            raise ValueError(f"facts[{index}] 必须是对象")
        for field in ("id", "statement", "source_path", "source_quote", "source_date", "scope"):
            if not str(fact.get(field) or "").strip():
                raise ValueError(f"facts[{index}] 缺字段 {field}")
        fid = str(fact["id"])
        if fid in fact_ids:
            raise ValueError(f"facts 出现重复 id：{fid}")
        fact_ids.add(fid)
        source = str(fact["source_path"])
        if not _allowed_source(source, meta):
            raise ValueError(f"facts[{index}].source_path 未登记或越过绑定知识库：{source}")
        allowed = fact.get("allowed_strong_words")
        if not isinstance(allowed, list) or any(word not in STRONG_WORDS for word in allowed):
            raise ValueError(
                f"facts[{index}].allowed_strong_words 只能从 {STRONG_WORDS} 选择"
            )
        statement = str(fact["statement"])
        for word in STRONG_WORDS:
            if word in statement and word not in allowed:
                raise ValueError(
                    f"facts[{index}] 使用强比较词「{word}」但未在 allowed_strong_words 授权"
                )

    if not isinstance(decision, dict):
        raise ValueError("decision.decision 必须是对象")
    if not attachment_fast_path:
        for field in ("communication_task", "category", "audience", "publisher",
                      "material_plan", "information_budget", "expression_bounds", "assumptions"):
            if field not in decision:
                raise ValueError(f"decision.decision 缺字段 {field}")
        if style_refs:
            decision = dict(decision)
            decision["style_refs"] = style_refs
    return {
        "interview": interview,
        "direction": direction,
        "facts": facts,
        "decision": decision,
    }


def save_decision(run_id: str, payload: Any, start=None) -> dict[str, Any]:
    run_dir = run_dir_for(run_id, start)
    meta = load_meta(run_id, start)
    if _meta_path(run_dir).name != "run.json":
        raise ValueError("旧版 meta.json 运行只读兼容，不能写入新版 decision")
    if meta.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("旧版运行只读兼容，不能改写方向或正文")
    data = _validate_decision(payload, meta)
    meta.update(data)
    meta["stage"] = 4
    meta["stage_name"] = "方向已确认"
    metrics = dict(meta.get("metrics") or {})
    saved = datetime.now()
    metrics["direction_saved_at"] = saved.isoformat(timespec="seconds")
    metrics["decision_saved_at"] = metrics["direction_saved_at"]
    meta["metrics"] = metrics
    _write_meta(run_dir, meta)
    return meta


def _validate_policy_hits(value: Any, delivery: str, field: str) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError(f"{field} 必须是数组")
    valid_ids = set(common_policy_rule_ids())
    hits: list[dict[str, str]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"{field}[{index}] 必须是对象")
        rule_id = str(item.get("rule_id") or "").strip()
        quote = str(item.get("quote") or "").strip()
        action = str(item.get("action") or "").strip()
        if rule_id not in valid_ids:
            raise ValueError(f"{field}[{index}].rule_id 不在通用规范中：{rule_id}")
        if not quote or quote not in delivery:
            raise ValueError(f"{field}[{index}].quote 无法定位到 delivery.md")
        if not action:
            raise ValueError(f"{field}[{index}] 缺 action")
        hit = {"rule_id": rule_id, "quote": quote, "action": action}
        reason = str(item.get("reason") or "").strip()
        if reason:
            hit["reason"] = reason
        hits.append(hit)
    return hits


def save_policy_check(run_id: str, payload: Any, start=None) -> dict[str, Any]:
    """把通用硬规则检查绑定到交付前的正文版本。

    Args:
        run_id: 已开启且已经保存方向的运行编号。
        payload: 结构化 ``hits``；可选 ``checked_rules`` 用于显式交叉校验。
        start: 业务项目根目录；省略时由工作空间自动定位。

    Returns:
        含规范版本、正文哈希、命中项和状态的检查回执。

    Raises:
        ValueError: 规则未逐条检查、命中项不合法、规范版本漂移或正文已交付。
        FileNotFoundError: 尚未写入 ``delivery.md``。
    """
    if not isinstance(payload, dict):
        raise ValueError("policy 输入顶层必须是对象")
    run_dir = run_dir_for(run_id, start)
    meta = load_meta(run_id, start)
    if _meta_path(run_dir).name != "run.json" or meta.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("旧版运行只读兼容，不能写入通用规范检查")
    if (meta.get("metrics") or {}).get("delivery_handoff_at"):
        raise ValueError("delivery.md 已交给老师；此后只允许保存受限核验问题")
    delivery_path = run_dir / "delivery.md"
    if not delivery_path.is_file():
        raise FileNotFoundError(f"缺 {delivery_path}，不能先检查后成稿")
    delivery = delivery_path.read_text(encoding="utf-8").strip()
    if not delivery:
        raise ValueError("delivery.md 为空，不能执行通用规范检查")

    current_version = common_policy_version()
    if str(meta.get("policy_version") or "") != current_version:
        raise ValueError(
            f"运行绑定的通用规范版本是 {meta.get('policy_version')}，当前为 {current_version}；"
            "请按当前规范重开运行"
        )
    expected = common_policy_rule_ids()
    checked = payload.get("checked_rules")
    checked_ids = [str(item) for item in checked] if isinstance(checked, list) else []
    if checked is not None and (
        not isinstance(checked, list)
        or len(checked_ids) != len(set(checked_ids))
        or set(checked_ids) != set(expected)
    ):
        raise ValueError(f"checked_rules 必须逐条包含 {expected}")
    hits = _validate_policy_hits(payload.get("hits"), delivery, "policy.hits")
    saved = datetime.now()
    data = {
        "policy_version": current_version,
        "delivery_sha256": _sha256(delivery_path),
        "checked_rules": list(expected),
        "hits": hits,
        "status": "blocked" if hits else "pass",
        "checked_at": saved.isoformat(timespec="seconds"),
    }
    meta["policy_check"] = data
    metrics = dict(meta.get("metrics") or {})
    metrics["policy_checked_at"] = data["checked_at"]
    meta["metrics"] = metrics
    _write_meta(run_dir, meta)
    return data


def handoff_delivery(run_id: str, start=None) -> dict[str, Any]:
    """登记并返回老师可立即修改的唯一正文；登记后当前智能体不得覆盖。"""
    run_dir = run_dir_for(run_id, start)
    meta = load_meta(run_id, start)
    if str(meta.get("mode") or "") != "生成":
        raise ValueError("只有生成任务可以交付可修改稿件")
    if meta.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("旧版运行只读兼容，不能改写正文或核验结果")
    direction = meta.get("direction")
    if not isinstance(direction, dict) or direction.get("confirmed_by_user") is not True:
        raise ValueError("方向尚未得到老师确认，不能交付稿件")
    delivery_path = run_dir / "delivery.md"
    if not delivery_path.is_file():
        raise FileNotFoundError(f"缺 {delivery_path}")
    delivery = delivery_path.read_text(encoding="utf-8").strip()
    if not delivery:
        raise ValueError("delivery.md 为空，不能交付稿件")

    current_hash = _sha256(delivery_path)
    metrics = dict(meta.get("metrics") or {})
    previous_hash = str(metrics.get("delivery_sha256") or "")
    if previous_hash and previous_hash != current_hash:
        raise ValueError(
            "可修改稿件交给老师后正文发生了变化；当前智能体不得自动覆盖，"
            "请只提供核验问题和局部替换建议"
        )
    policy_check = meta.get("policy_check")
    if not isinstance(policy_check, dict):
        raise ValueError("交付前必须先保存通用规范检查：save --kind policy")
    if str(policy_check.get("policy_version") or "") != str(meta.get("policy_version") or ""):
        raise ValueError("通用规范检查版本与本次运行不一致，请重新检查")
    current_policy_version = common_policy_version()
    if str(meta.get("policy_version") or "") != current_policy_version:
        raise ValueError(
            f"通用规范已从 {meta.get('policy_version')} 更新为 {current_policy_version}；"
            "请按当前规范重开运行"
        )
    if str(policy_check.get("delivery_sha256") or "") != current_hash:
        raise ValueError("delivery.md 在通用规范检查后发生变化，请重新执行 save --kind policy")
    checked_rules = [str(item) for item in (policy_check.get("checked_rules") or [])]
    if len(checked_rules) != len(set(checked_rules)) \
            or set(checked_rules) != set(common_policy_rule_ids()):
        raise ValueError("通用硬规则没有逐条检查完整，请重新执行 save --kind policy")
    if policy_check.get("status") != "pass" or policy_check.get("hits"):
        raise ValueError("通用规范仍有命中项，修复正文并重新检查后才能 handoff")

    now = datetime.now()
    if not metrics.get("delivery_handoff_at"):
        metrics["delivery_handoff_at"] = now.isoformat(timespec="seconds")
        metrics["delivery_sha256"] = current_hash
        metrics["open_to_delivery_seconds"] = _elapsed_seconds(meta.get("started_at"), now)
        metrics["direction_to_delivery_seconds"] = _elapsed_seconds(
            metrics.get("direction_saved_at"), now
        )
    meta["metrics"] = metrics
    if int(meta.get("stage") or 0) < 5:
        meta["stage"] = 5
        meta["stage_name"] = STAGE_NAMES[5]
    _write_meta(run_dir, meta)
    return {
        "run_id": run_id,
        "delivery_path": str(delivery_path),
        "delivery_sha256": current_hash,
        "delivery": delivery,
        "risk_sentences": draft_risk_sentences(delivery),
        "metrics": metrics,
    }


def _expected_verdict(payload: dict[str, Any]) -> str:
    claims = [item for item in (payload.get("claims") or []) if isinstance(item, dict)]
    judgements = [str(item.get("judgement") or "") for item in claims]
    if "unsourced" in judgements or payload.get("cross_brand") or payload.get("redline_hits"):
        return "暂不建议提交"
    if "drifted" in judgements or "stale" in judgements:
        return "有待确认项"
    return "可进入人工初审"


def _validate_verify(payload: Any, meta: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("verify 输入顶层必须是对象")
    if meta.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("旧版运行只读兼容，不能改写正文或核验结果")
    if not (run_dir / "delivery.md").is_file():
        raise ValueError("缺 delivery.md，不能先核验后成稿")
    claims = payload.get("claims")
    sources = payload.get("sources_used")
    delivery = (run_dir / "delivery.md").read_text(encoding="utf-8")
    current_hash = _sha256(run_dir / "delivery.md")
    handed_hash = str((meta.get("metrics") or {}).get("delivery_sha256") or "")
    if handed_hash and handed_hash != current_hash:
        raise ValueError(
            "可修改稿件交付后 delivery.md 已变化；本轮核验不能冒充当前稿，也不能自动覆盖老师修改"
        )

    compact_review = claims is None and isinstance(payload.get("issues"), list)
    risks = draft_risk_sentences(delivery)
    if compact_review:
        if not handed_hash:
            raise ValueError("轻量核验前必须先运行 handoff，把可修改稿件交给老师")
        risk_by_id = {str(item["id"]): item for item in risks}
        resolved_claims: list[dict[str, Any]] = []
        for index, issue in enumerate(payload.get("issues") or []):
            if not isinstance(issue, dict):
                raise ValueError(f"issues[{index}] 必须是对象")
            judgement = str(issue.get("judgement") or "")
            if judgement not in ("drifted", "unsourced", "stale"):
                raise ValueError(
                    f"issues[{index}].judgement 必须是 drifted、unsourced 或 stale 之一"
                )
            risk_id = str(issue.get("risk_id") or "").strip()
            fragment = str(issue.get("quote") or "").strip()
            risk = risk_by_id.get(risk_id) if risk_id else None
            if risk is None and fragment:
                matches = [item for item in risks if fragment in str(item["quote"])]
                if len(matches) == 1:
                    risk = matches[0]
            if risk is None and fragment:
                matches = [quote for quote in _draft_sentences(delivery) if fragment in quote]
                if len(matches) == 1:
                    risk = {
                        "id": f"R{len(risks) + 1}",
                        "quote": matches[0],
                        "signals": ["模型识别问题"],
                    }
                    risks.append(risk)
                    risk_by_id[str(risk["id"])] = risk
            if risk is None:
                raise ValueError(f"issues[{index}] 无法唯一定位到初稿高风险句")
            action = str(issue.get("action") or "").strip()
            if not action:
                raise ValueError(f"issues[{index}] 缺局部修改建议 action")
            resolved_claims.append({
                "quote": risk["quote"],
                "risk": str(issue.get("risk") or "、".join(risk["signals"])),
                "fact_ids": [],
                "judgement": judgement,
                "source_quote": str(issue.get("source_quote") or ""),
                "action": action,
            })
        claims = resolved_claims
        if sources is None and meta.get("evidence_boundary") == "attachments":
            sources = [
                {"path_or_url": path, "kind": "需求材料", "date": ""}
                for path in attachment_paths(meta)
            ]
    elif not isinstance(claims, list):
        raise ValueError("verify 需要 claims 数组，或使用轻量 issues 数组")

    facts_by_id = {
        str(fact.get("id")): fact for fact in (meta.get("facts") or [])
        if isinstance(fact, dict) and fact.get("id")
    }
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            raise ValueError(f"claims[{index}] 必须是对象")
        judgement = str(claim.get("judgement") or "")
        if judgement not in JUDGEMENTS:
            raise ValueError(f"claims[{index}].judgement 必须是 {JUDGEMENTS} 之一")
        quote = str(claim.get("quote") or "").strip()
        if not quote:
            raise ValueError(f"claims[{index}] 缺正文 quote")
        if quote not in delivery:
            raise ValueError(f"claims[{index}].quote 不在 delivery.md 中")
        fact_ids = claim.get("fact_ids") or []
        if not isinstance(fact_ids, list) or any(str(fid) not in facts_by_id for fid in fact_ids):
            raise ValueError(f"claims[{index}].fact_ids 含 run.json 中不存在的事实 id")
    if not compact_review:
        for word in STRONG_WORDS:
            if word not in delivery:
                continue
            matches = [claim for claim in claims if word in str(claim.get("quote") or "")]
            if not matches:
                raise ValueError(f"正文使用强比较词「{word}」，verify.claims 必须逐句复核")
            for claim in matches:
                authorized = any(
                    word in (facts_by_id[str(fid)].get("allowed_strong_words") or [])
                    for fid in (claim.get("fact_ids") or []) if str(fid) in facts_by_id
                )
                if not authorized:
                    raise ValueError(
                        f"核验句使用强比较词「{word}」，但引用事实没有授权该比较范围"
                    )
    if not isinstance(sources, list) or not sources:
        raise ValueError("verify.sources_used 至少包含一条本稿实际来源")
    allowed_sources = {
        str(fact.get("source_path")) for fact in (meta.get("facts") or [])
        if isinstance(fact, dict) and fact.get("source_path")
    }
    for index, item in enumerate(sources):
        if not isinstance(item, dict) or not str(item.get("path_or_url") or "").strip():
            raise ValueError(f"sources_used[{index}] 缺 path_or_url")
        source = str(item["path_or_url"])
        if source not in allowed_sources and not _allowed_source(source, meta):
            raise ValueError(f"sources_used[{index}] 未登记或越界：{source}")

    cross_brand = payload.get("cross_brand") or []
    redline_hits = _validate_policy_hits(
        payload.get("redline_hits") or [], delivery, "redline_hits"
    )
    editorial_risks = payload.get("editorial_risks") or []
    if not all(isinstance(value, list) for value in (cross_brand, redline_hits, editorial_risks)):
        raise ValueError("cross_brand、redline_hits、editorial_risks 必须是数组")
    if len(editorial_risks) > 3:
        raise ValueError("editorial_risks 最多三条")

    counts = {name: 0 for name in JUDGEMENTS}
    for claim in claims:
        counts[str(claim["judgement"])] += 1
    data = {
        "role": "single-agent-verifier",
        "run_id": meta.get("run_id"),
        "claims": claims,
        "coverage": (
            {"total_risks": len(risks), "reviewed_risks": len(risks),
             "issues": len(claims), **counts}
            if compact_review else {"total_claims": len(claims), **counts}
        ),
        "review_mode": "issues-only-after-handoff" if compact_review else "claim-map",
        "risk_sentences": risks if compact_review else [],
        "delivery_sha256": current_hash,
        "policy_version": meta.get("policy_version"),
        "cross_brand": cross_brand,
        "redline_hits": redline_hits,
        "editorial_risks": editorial_risks,
        "sources_used": sources,
    }
    data["verdict"] = _expected_verdict(data)
    return data


def save_verify(run_id: str, payload: Any, start=None) -> dict[str, Any]:
    run_dir = run_dir_for(run_id, start)
    meta = load_meta(run_id, start)
    if _meta_path(run_dir).name != "run.json":
        raise ValueError("旧版 meta.json 运行只读兼容，不能写入新版 verify")
    data = _validate_verify(payload, meta, run_dir)
    _atomic_json(run_dir / "verify.json", data)
    meta["stage"] = 6
    meta["stage_name"] = "高风险句已复核"
    metrics = dict(meta.get("metrics") or {})
    saved = datetime.now()
    metrics["verify_saved_at"] = saved.isoformat(timespec="seconds")
    metrics["delivery_to_verify_seconds"] = _elapsed_seconds(
        metrics.get("delivery_handoff_at"), saved
    )
    meta["metrics"] = metrics
    _write_meta(run_dir, meta)
    return data


def save_payload(run_id: str, kind: str, payload: Any, start=None) -> dict[str, Any]:
    if kind == "decision":
        return save_decision(run_id, payload, start)
    if kind == "policy":
        return save_policy_check(run_id, payload, start)
    if kind == "verify":
        return save_verify(run_id, payload, start)
    raise ValueError("kind 只能是 decision、policy 或 verify")


def build_delivery_check(run_id: str, start=None) -> Path:
    run_dir = run_dir_for(run_id, start)
    delivery_path = run_dir / "delivery.md"
    verify_path = run_dir / "verify.json"
    if not delivery_path.is_file():
        raise FileNotFoundError(f"缺 {delivery_path}")
    if not verify_path.is_file():
        raise FileNotFoundError(f"缺 {verify_path}")
    meta = load_meta(run_id, start)
    if meta.get("schema_version") != CURRENT_SCHEMA_VERSION:
        raise ValueError("旧版运行只读兼容，不能生成新版交付核对卡")
    verify = json.loads(verify_path.read_text(encoding="utf-8"))
    if not isinstance(verify, dict) or verify.get("verdict") not in VERDICTS:
        raise ValueError("verify.json 缺合法 verdict")
    verified_hash = str(verify.get("delivery_sha256") or "")
    if not verified_hash:
        raise ValueError("verify.json 缺 delivery_sha256")
    if verified_hash != _sha256(delivery_path):
        raise ValueError(
            "delivery.md 在核验后发生了变化；不会把旧核验结论用于老师修改后的正文"
        )

    issues: list[str] = []
    for claim in verify.get("claims") or []:
        if not isinstance(claim, dict) or claim.get("judgement") == "matched":
            continue
        action = str(claim.get("action") or "请核对来源后处理")
        issues.append(
            f"- {claim.get('judgement')}｜{str(claim.get('quote') or '')}｜{action}"
        )
    for label, key in (("跨品牌", "cross_brand"), ("红线", "redline_hits")):
        for item in verify.get(key) or []:
            if label == "红线" and isinstance(item, dict):
                issues.append(
                    f"- 红线｜{item.get('rule_id')}｜{item.get('quote')}｜{item.get('action')}"
                )
            else:
                issues.append(
                    f"- {label}｜{item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}"
                )
    for item in verify.get("editorial_risks") or []:
        issues.append(
            f"- 编辑风险｜{item if isinstance(item, str) else json.dumps(item, ensure_ascii=False)}"
        )

    source_lines: list[str] = []
    for item in verify.get("sources_used") or []:
        if not isinstance(item, dict):
            continue
        raw = str(item.get("path_or_url") or "")
        label = raw if raw.startswith(("http://", "https://")) else Path(raw).name
        kind = str(item.get("kind") or "来源")
        date = str(item.get("date") or "").strip()
        source_lines.append(f"- {kind}：{label}" + (f"（{date}）" if date else ""))

    style_lines: list[str] = []
    decision = meta.get("decision")
    if isinstance(decision, dict):
        for item in decision.get("style_refs") or []:
            if not isinstance(item, dict):
                continue
            label = Path(str(item.get("path") or "")).name
            why = str(item.get("why") or "历史稿件")
            caveat = str(item.get("caveat") or "只参考表达，不作为事实来源")
            style_lines.append(f"- {label}：{why}；{caveat}")

    card = f"{verify['verdict']}。"
    if issues:
        card += "\n" + "\n".join(issues[:5])
    else:
        card += "核心事实均有来源，未发现跨品牌信息和待确认事实。"
    delivery_check = (
        "# 交付核对卡\n\n结论："
        + card
        + "\n\n## 实际来源\n\n"
        + "\n".join(source_lines)
        + (("\n\n## 风格参考\n\n" + "\n".join(style_lines)) if style_lines else "")
        + "\n"
    )
    target = run_dir / "delivery-check.md"
    _atomic_text(target, delivery_check)
    meta["stage"] = 8
    meta["stage_name"] = STAGE_NAMES[8]
    metrics = dict(meta.get("metrics") or {})
    metrics["delivery_check_saved_at"] = datetime.now().isoformat(timespec="seconds")
    meta["metrics"] = metrics
    _write_meta(run_dir, meta)
    return target


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
    meta["stage"] = 9
    meta["stage_name"] = STAGE_NAMES[9]
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
        return _clean_meta(json.loads(_meta_path(candidates[-1]).read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError):
        return None


def present_artifacts(run_dir: Path) -> set[str]:
    return {p.name for p in run_dir.iterdir() if p.is_file()} if run_dir.is_dir() else set()


def reached_stage(run_dir: Path) -> int:
    """按产出文件判断这次运行实际走到了哪一步。"""
    present = present_artifacts(run_dir)
    reached = 0
    schema_version = None
    if "run.json" in present:
        try:
            record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            reached = int(record.get("stage") or 0) if isinstance(record, dict) else 0
            schema_version = record.get("schema_version") if isinstance(record, dict) else None
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            reached = 0
    artifacts = (
        STAGE_ARTIFACTS
        if schema_version == CURRENT_SCHEMA_VERSION
        else SCHEMA_5_STAGE_ARTIFACTS if schema_version == 5 else SCHEMA_4_STAGE_ARTIFACTS
    )
    for stage in sorted(artifacts):
        if any(name in present for name in artifacts[stage]):
            reached = max(reached, stage)
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
