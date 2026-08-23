#!/usr/bin/env python3
"""条件化记忆：带置信度、触发条件和反例的知识，而不是规则清单。

规则清单的失败模式是可预测的：只增不减、越长越没人看、最后每条都在某些场合是
错的，而系统无法判断"这次算不算"。所以这里存的每条知识都必须带触发条件和不适
用范围，并且可以被反例修正。

``methodology`` 级的候选永不写入 ``memory.json``，只落在
``methodology-candidates.json``。任何工作空间都不允许自动改写通用方法论。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import workspace

MEMORY_VERSION = 4
MEMORY_FILE = "memory.json"
METHODOLOGY_FILE = "methodology-candidates.json"
MEMORY_FIELDS = (
    "id", "scope", "knowledge", "trigger", "not_applicable", "evidence",
    "evidence_count", "distinct_events", "counterexamples", "confidence",
    "status", "created", "updated", "last_hit", "source_run", "cancellations",
    "retired_why",
)

CONFIDENCE_MAX = 0.9          # 永不到 1.0：任何记忆都可能在下一个场景里失效
HIGH_THRESHOLD = 0.65         # ≥ 此值可自动进入写作程序（但必须可见、可取消）
MID_THRESHOLD = 0.35          # ≥ 此值可作推荐或 A/B 备选

INITIAL_CONFIDENCE = {"high": 0.5, "medium": 0.35, "low": 0.2}
DELTA_SAME_EVENT = 0.05       # 同一传播事件内的同向证据
DELTA_NEW_EVENT = 0.15        # 独立传播事件的同向证据
DELTA_COUNTER = -0.25         # 反例
DELTA_CANCELLED = -0.10       # 老师在决策卡里取消了这一项
DELTA_DECAY = -0.10           # 长期未被命中
DECAY_AFTER_DAYS = 183

VALID_SCOPES = ("session", "workspace", "brand", "methodology")

class MemoryError_(RuntimeError):
    """记忆库损坏，或写入了不允许的内容。"""


def _memory_path(start=None) -> Path:
    return workspace.learning_dir(start) / MEMORY_FILE


def _methodology_path(start=None) -> Path:
    return workspace.learning_dir(start) / METHODOLOGY_FILE


def _load_json(path: Path, fallback: Any) -> Any:
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise MemoryError_(f"{path} 解析失败：{exc}") from exc


def _save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load(start=None) -> dict[str, Any]:
    data = _load_json(_memory_path(start), {"version": MEMORY_VERSION, "items": []})
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise MemoryError_(f"{_memory_path(start)} 结构不是 {{version, items}}")
    source_version = int(data.get("version") or 0)
    items: list[dict[str, Any]] = []
    for item in data["items"]:
        if not isinstance(item, dict):
            continue
        record = {field: item[field] for field in MEMORY_FIELDS if field in item}
        if record.get("scope") not in VALID_SCOPES:
            if source_version < MEMORY_VERSION and record.get("scope"):
                record["scope"] = "workspace"
            else:
                raise MemoryError_(f"记忆 {record.get('id')} 的 scope 不合法：{record.get('scope')!r}")
        items.append(record)
    cleaned = {
        "version": MEMORY_VERSION,
        "items": items,
    }
    if data.get("brand"):
        cleaned["brand"] = data["brand"]
    return cleaned


def run_exists(run_id: str, start=None) -> bool:
    """这个 ``run_id`` 是否有真实的运行记录。

    独立证据必须指向一次真实运行。允许引用不存在的 run，等于允许凭空制造"多个
    独立任务中重复出现"——而置信度全靠这个计数，编出来的重复会把一次偶发偏好直接
    推到高置信度并自动进入写作程序。
    """
    if not run_id:
        return False
    return (workspace.runs_dir(start) / run_id).is_dir()


def save(data: dict[str, Any], start=None) -> None:
    bad = [i.get("id") for i in data.get("items", []) if i.get("scope") == "methodology"]
    if bad:
        raise MemoryError_(
            f"methodology 级候选不允许写入 {MEMORY_FILE}（{bad}）。"
            f"它们只记录在 {METHODOLOGY_FILE}，由维护者手工评审。"
        )
    _save_json(_memory_path(start), data)


def unique_id(known: set[str], when: date | None = None) -> str:
    """生成一个当天序号最小且未被占用的记忆 id。

    唯一性是必需的：重复 id 会让 confirm / counter / reinforce 作用到错误的那一条，
    而症状是"我确认过了却没生效"——最难查的一类问题。
    """
    today = (when or date.today()).isoformat()
    n = 1
    while f"M-{today}-{n:02d}" in known:
        n += 1
    return f"M-{today}-{n:02d}"


def _normalise(candidate: dict[str, Any], brand: str, known: set[str]) -> dict[str, Any]:
    scope = candidate.get("scope") or "session"
    if scope not in VALID_SCOPES:
        raise MemoryError_(f"scope 必须是 {VALID_SCOPES} 之一，收到 {scope!r}")
    strength = candidate.get("evidence_strength") or "low"
    trigger = dict(candidate.get("trigger") or {})
    trigger.setdefault("brand", brand)
    confidence = candidate.get("confidence")
    if confidence is None:
        confidence = INITIAL_CONFIDENCE.get(strength, 0.2)
    now = datetime.now().date().isoformat()
    return {
        "id": candidate.get("id") or unique_id(known),
        "scope": scope,
        "knowledge": (candidate.get("knowledge") or "").strip(),
        "trigger": trigger,
        "not_applicable": list(candidate.get("not_applicable") or []),
        "evidence": list(candidate.get("evidence") or []),
        "evidence_count": int(candidate.get("evidence_count") or 1),
        "distinct_events": int(candidate.get("distinct_events") or 1),
        "counterexamples": list(candidate.get("counterexamples") or []),
        "confidence": min(CONFIDENCE_MAX, round(float(confidence), 2)),
        "status": "pending_confirmation" if scope in ("workspace", "brand") else "active",
        "created": candidate.get("created") or now,
        "updated": now,
        "last_hit": candidate.get("last_hit"),
        "source_run": candidate.get("source_run"),
    }


def add_candidates(
    candidates: list[dict[str, Any]], *, brand: str | None = None,
    run_id: str | None = None, start=None,
) -> dict[str, Any]:
    """把反馈归因员的候选写入记忆库。``methodology`` 级分流到单独文件。"""
    if brand is None:
        ws = workspace.load(start)
        brand = str(ws["brand"])
    store = load(start)
    store.setdefault("brand", brand)
    items: list[dict[str, Any]] = store["items"]

    accepted: list[str] = []
    routed: list[str] = []
    rejected: list[dict[str, str]] = []
    renamed: list[dict[str, str]] = []
    known_ids = {str(item.get("id")) for item in items}

    for candidate in candidates:
        knowledge = (candidate.get("knowledge") or "").strip()
        if not knowledge:
            rejected.append({"id": str(candidate.get("id")), "why": "knowledge 为空"})
            continue
        if run_id:
            candidate.setdefault("source_run", run_id)
        if candidate.get("scope") == "methodology":
            pool = _load_json(_methodology_path(start), {"version": MEMORY_VERSION, "items": []})
            pool.setdefault("items", [])
            pool_ids = {str(i.get("id")) for i in pool["items"]}
            pool["items"].append(
                {
                    "id": candidate.get("id") if str(candidate.get("id")) not in pool_ids
                          else unique_id(pool_ids),
                    "note": knowledge,
                    "brand": brand,
                    "source_run": candidate.get("source_run"),
                    "recorded": datetime.now().date().isoformat(),
                }
            )
            _save_json(_methodology_path(start), pool)
            routed.append(knowledge[:40])
            continue

        # 重复 id 会让 confirm / counter 作用到错误的那一条，必须改名而不是共存
        wanted = candidate.get("id")
        if wanted and str(wanted) in known_ids:
            fresh = unique_id(known_ids)
            renamed.append({"from": str(wanted), "to": fresh})
            candidate = {**candidate, "id": fresh}

        record = _normalise(candidate, str(brand), known_ids)
        if not record["not_applicable"] and record["scope"] != "session":
            rejected.append(
                {"id": record["id"], "why": "缺 not_applicable：没有不适用范围的知识会被到处滥用"}
            )
            continue
        items.append(record)
        known_ids.add(record["id"])
        accepted.append(record["id"])

    save(store, start)
    return {
        "accepted": accepted,
        "routed_to_methodology": routed,
        "renamed": renamed,
        "rejected": rejected,
        "total": len(items),
    }


def add_note(scope: str, note: str, *, brand: str | None = None,
             run_id: str | None = None, start=None):
    """直接记一条候选。多用于 ``methodology`` 级的"定位不了"记录。"""
    return add_candidates(
        [
            {
                "scope": scope,
                "knowledge": note,
                "evidence_strength": "low",
                "not_applicable": ["待补充"] if scope != "session" else [],
            }
        ],
        brand=brand,
        run_id=run_id,
        start=start,
    )


def _find(items: list[dict[str, Any]], memory_id: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == memory_id:
            return item
    raise MemoryError_(f"找不到记忆 {memory_id}")


def confirm(memory_id: str, start=None) -> dict[str, Any]:
    """确认一次，``workspace`` / ``brand`` 级由此生效。"""
    store = load(start)
    item = _find(store["items"], memory_id)
    item["status"] = "active"
    item["updated"] = datetime.now().date().isoformat()
    save(store, start)
    return item


def reinforce(memory_id: str, *, run_id: str, new_event: bool = True, start=None) -> dict[str, Any]:
    """同向证据：独立事件 +0.15，同一事件 +0.05。

    声明为独立事件时，``run_id`` 必须指向一次真实运行——否则"多个独立任务中重复
    出现"就可以凭空编出来，而置信度完全依赖这个计数。
    """
    if new_event and not run_exists(run_id, start):
        raise MemoryError_(
            f"独立事件必须引用一次真实运行，但 {run_id or '（空）'} 没有运行记录。"
            f"同一场活动内的同向修改请加 --same-event（只 +0.05）。"
        )
    store = load(start)
    item = _find(store["items"], memory_id)
    delta = DELTA_NEW_EVENT if new_event else DELTA_SAME_EVENT
    item["evidence_count"] = int(item.get("evidence_count") or 0) + 1
    if new_event:
        item["distinct_events"] = int(item.get("distinct_events") or 0) + 1
    item["confidence"] = min(CONFIDENCE_MAX, round(float(item["confidence"]) + delta, 2))
    item["last_hit"] = run_id
    item["updated"] = datetime.now().date().isoformat()
    save(store, start)
    return item


def counterexample(memory_id: str, *, run_id: str, note: str, narrow: str = "", start=None):
    """反例：降低置信度或缩小适用范围，**不删旧结论**。"""
    store = load(start)
    item = _find(store["items"], memory_id)
    item.setdefault("counterexamples", []).append(
        {"run_id": run_id, "note": note, "at": datetime.now().date().isoformat()}
    )
    if narrow:
        item.setdefault("not_applicable", []).append(narrow)
    item["confidence"] = max(0.0, round(float(item["confidence"]) + DELTA_COUNTER, 2))
    item["updated"] = datetime.now().date().isoformat()
    save(store, start)
    return item


def cancelled(memory_id: str, *, run_id: str, start=None) -> dict[str, Any]:
    """老师在决策卡里取消了这一项：弱反例，只 -0.10。"""
    store = load(start)
    item = _find(store["items"], memory_id)
    item["confidence"] = max(0.0, round(float(item["confidence"]) + DELTA_CANCELLED, 2))
    item.setdefault("cancellations", []).append(
        {"run_id": run_id, "at": datetime.now().date().isoformat()}
    )
    item["updated"] = datetime.now().date().isoformat()
    save(store, start)
    return item


def retire(memory_id: str, *, why: str = "", start=None) -> dict[str, Any]:
    """老师明确否定：置 0 并标 retired，但保留记录。"""
    store = load(start)
    item = _find(store["items"], memory_id)
    item["confidence"] = 0.0
    item["status"] = "retired"
    item["retired_why"] = why
    item["updated"] = datetime.now().date().isoformat()
    save(store, start)
    return item


def decay(start=None, today: date | None = None) -> dict[str, Any]:
    """长期未命中的记忆按 -0.10 衰减。建索引时结算一次。"""
    store = load(start)
    now = today or date.today()
    touched: list[str] = []
    for item in store["items"]:
        stamp = item.get("updated") or item.get("created")
        if not stamp:
            continue
        try:
            age = (now - date.fromisoformat(str(stamp))).days
        except ValueError:
            continue
        if age >= DECAY_AFTER_DAYS and item.get("status") != "retired":
            item["confidence"] = max(0.0, round(float(item["confidence"]) + DELTA_DECAY, 2))
            item["updated"] = now.isoformat()
            touched.append(item["id"])
    if touched:
        save(store, start)
    return {"decayed": touched, "total": len(store["items"])}


def tier(confidence: float) -> str:
    if confidence >= HIGH_THRESHOLD:
        return "高"
    if confidence >= MID_THRESHOLD:
        return "中"
    return "低"


def listing(
    *, brand: str | None = None, scope: str | None = None,
    min_confidence: float | None = None, include_retired: bool = False, start=None,
) -> dict[str, Any]:
    """按条件列出记忆，附带分级与用法说明。"""
    store = load(start)
    out: list[dict[str, Any]] = []
    for item in store["items"]:
        if brand and (item.get("trigger") or {}).get("brand") not in (None, brand):
            continue
        if scope and item.get("scope") != scope:
            continue
        if not include_retired and item.get("status") == "retired":
            continue
        confidence = float(item.get("confidence") or 0)
        if min_confidence is not None and confidence < min_confidence:
            continue
        level = tier(confidence)
        out.append(
            {
                **item,
                "tier": level,
                "usage": {
                    "高": "可自动进入本次写作程序，但必须在决策卡中可见、可取消",
                    "中": "只作推荐或 A/B 备选，不直接控制正文",
                    "低": "只作研究线索，不影响正式生成",
                }[level],
            }
        )
    out.sort(key=lambda i: -float(i.get("confidence") or 0))
    methodology = _load_json(_methodology_path(start), {"items": []}).get("items", [])
    return {
        "brand": brand or store.get("brand"),
        "count": len(out),
        "items": out,
        "pending_confirmation": [i["id"] for i in out if i.get("status") == "pending_confirmation"],
        "methodology_candidates": len(methodology),
    }
