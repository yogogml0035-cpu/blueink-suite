#!/usr/bin/env python3
"""按任务检索证据切片：返回候选文件清单，不返回全文。

三轨取证的实现点。事实、风格、编辑策略需要的不是同一批证据，所以 ``--track``
会改变排序与过滤规则，而不只是改变权重：

- ``fact``     优先原文资产与需求素材，初稿降权
- ``style``    只返回终稿与成品参考，排除初稿和对照文件
- ``strategy`` 只返回初终稿对比、经验总结与反馈

检索永远限制在绑定的知识库之内——因为它只从索引里查，而索引只包含 kb_root 下
的文件。这不是一条需要反复申明的规则，而是实现方式。

知识库里混进来的写作技能包（``instruction_artifact``）默认不返回。它们是那些技能的
提示词与固定模板，取出来当经验证据就等于让外部固定结构接管本次判断。只有明确要审计
这些技能包时才用 ``--include-instruction-artifacts``。
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import index_kb
import workspace

TRACK_ALLOWED_EVIDENCE = {
    "style": {"成品参考"},
    "strategy": {"初终稿对比", "经验总结", "反馈"},
}

_CJK = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


BIGRAM_WEIGHT = 0.35  # 中文滑窗项只用来兜底召回，不能让长查询词凭滑窗数量刷分


def _terms(query: str) -> list[tuple[str, float]]:
    """把查询拆成 (检索项, 权重)。

    中文没有空格，整串匹配不上时靠 2 字滑窗兜底召回。但滑窗项必须降权：
    「平台能力」会派生出 3 个滑窗项，不降权的话它的得分会是单字词的 4 倍，
    排序就变成了"谁的查询词长谁赢"。
    """
    raw = [t for t in re.split(r"[\s,，、;；/]+", query or "") if t]
    terms: list[tuple[str, float]] = []
    seen: set[str] = set()

    def push(term: str, weight: float) -> None:
        if term and term not in seen:
            seen.add(term)
            terms.append((term, weight))

    for token in raw:
        token = token.strip().lower()
        if not token:
            continue
        push(token, 1.0)
        if _CJK.search(token) and len(token) > 2:
            for i in range(len(token) - 1):
                push(token[i:i + 2], BIGRAM_WEIGHT)
    return terms


def _term_score(record: dict[str, Any], terms: list[tuple[str, float]]) -> tuple[float, list[str]]:
    if not terms:
        return 0.0, []
    title = (record.get("title") or "").lower()
    summary = (record.get("summary") or "").lower()
    path = (record.get("path") or "").lower()
    entities = " ".join(record.get("entities") or []).lower()
    score = 0.0
    why: list[str] = []
    for term, weight in terms:
        if term in title:
            score += 3.0 * weight
            if weight == 1.0:
                why.append(f"标题含「{term}」")
        elif term in entities:
            score += 2.5 * weight
            if weight == 1.0:
                why.append(f"实体含「{term}」")
        elif term in path:
            score += 2.0 * weight
            if weight == 1.0:
                why.append(f"路径含「{term}」")
        elif term in summary:
            score += 1.0 * weight
            if weight == 1.0:
                why.append(f"摘要含「{term}」")
    if not why and score > 0:
        why.append("仅中文滑窗项命中，相关度较弱")
    return score, why[:4]


def _track_adjust(record: dict[str, Any], track: str | None) -> float | None:
    """按轨调整得分。返回 ``None`` 表示该轨不接受这条记录。"""
    evidence = set(record.get("evidence_type") or [])
    stage = record.get("stage") or "未知"

    if track == "style":
        # 初稿是被客户改掉的表达，对照文件是两份稿的拼合，都不是风格样本
        if stage in ("初稿", "对照"):
            return None
        if stage == "终稿":
            return 3.0
        if evidence & TRACK_ALLOWED_EVIDENCE["style"]:
            return 2.0
        return None

    if track == "strategy":
        if not evidence & TRACK_ALLOWED_EVIDENCE["strategy"] and stage != "对照":
            return None
        bonus = 3.0 if evidence & {"初终稿对比", "经验总结"} else 1.5
        return bonus

    if track == "fact":
        bonus = 0.0
        if evidence & {"原文资产", "需求素材"}:
            bonus += 3.0
        if stage == "初稿":
            bonus -= 2.0
        return bonus

    return 0.0


def _recency(record: dict[str, Any], since: str | None) -> tuple[float, bool]:
    """时间加权。索引里的 date 可能被手工编辑坏，坏了就当没有日期，不崩。"""
    date = record.get("date")
    if since and (not date or str(date) < since):
        return 0.0, False
    try:
        year = int(str(date)[:4])
    except (TypeError, ValueError):
        return 0.0, True
    return max(0.0, (year - 2023) * 0.5), True


def search(
    query: str = "",
    *,
    category: str | None = None,
    track: str | None = None,
    limit: int = 12,
    since: str | None = None,
    loose: bool = False,
    include_instruction_artifacts: bool = False,
    start=None,
) -> dict[str, Any]:
    """在索引里检索候选证据切片。"""
    ws = workspace.load(start)
    current_root = workspace.kb_root(start)
    index = index_kb.load_index(start)
    files = index.get("files") or []
    if files:
        index_root = Path(str(index.get("kb_root") or "")).expanduser().resolve()
        mismatches: list[str] = []
        if os.path.normcase(str(index_root)) != os.path.normcase(str(current_root)):
            mismatches.append(f"知识库根 {index_root} != {current_root}")
        if str(index.get("brand") or "") != str(ws.get("brand") or ""):
            mismatches.append(f"品牌 {index.get('brand')} != {ws.get('brand')}")
        if str(index.get("teacher") or "") != str(ws.get("teacher") or ""):
            mismatches.append(f"老师 {index.get('teacher') or '未记名'} != {ws.get('teacher')}")
        if mismatches:
            raise workspace.WorkspaceError(
                "当前索引不属于这个工作空间：" + "；".join(mismatches)
                + "。请运行 index --full 重建，旧索引不会参与检索。"
            )
    if not files:
        return {
            "brand": index.get("brand"),
            "query": query,
            "track": track,
            "hits": [],
            "note": "索引为空。先运行 index，再检索。",
        }

    terms = _terms(query)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    excluded_instruction = 0

    for record in files:
        # 混进来的技能包默认不参与检索。它们是提示词和固定模板，被当成经验
        # 证据取出来就等于让外部固定结构接管本次判断——这正是"结构固化"的来源。
        if record.get("instruction_artifact") and not include_instruction_artifacts:
            excluded_instruction += 1
            continue
        adjust = _track_adjust(record, track)
        if adjust is None:
            continue
        recency_bonus, date_ok = _recency(record, since)
        if not date_ok:
            continue

        why: list[str] = []
        score, term_why = _term_score(record, terms)
        why.extend(term_why)

        if category:
            cats = record.get("categories") or []
            cands = record.get("candidates") or []
            if category in cats:
                score += 4.0
                why.append(f"品类命中「{category}」")
            elif category in cands:
                score += 2.0
                why.append(f"候选品类含「{category}」")
            elif not loose:
                continue

        if not terms and not category:
            score += 1.0  # 无查询词时按轨与时间排序

        score += adjust + recency_bonus
        if adjust:
            why.append(f"{track} 轨加权 {adjust:+.1f}")
        score *= 0.6 + 0.4 * float(record.get("confidence") or 0.3)

        if score <= 0:
            continue
        scored.append((score, record, why))

    scored.sort(key=lambda item: (-item[0], item[1].get("path", "")))
    hits = [
        {
            "path": record["path"],
            "score": round(score, 2),
            "evidence_type": record.get("evidence_type"),
            "stage": record.get("stage"),
            "categories": record.get("categories"),
            "date": record.get("date"),
            "title": record.get("title"),
            "summary": record.get("summary"),
            "confidence": record.get("confidence"),
            "extractable": record.get("extractable"),
            "content_status": record.get("content_status", "text" if record.get("extractable") else "metadata_only"),
            "why": why,
        }
        for score, record, why in scored[:limit]
    ]

    return {
        "brand": index.get("brand"),
        "teacher": index.get("teacher") or "",
        "kb_root": index.get("kb_root"),
        "query": query,
        "category": category,
        "track": track,
        "limit": limit,
        "candidates_total": len(scored),
        "excluded_instruction_artifacts": excluded_instruction,
        "hits": hits,
        "note": "" if hits else "没有命中。放宽 --category 或去掉 --track 再试；仍为空时先跑 doctor。",
    }


def record_to_run(result: dict[str, Any], run_id: str, start=None) -> str | None:
    """把检索结果追加到运行记录，供事后定位「这次到底检索了什么」。"""
    run_dir = workspace.runs_dir(start) / run_id
    if not run_dir.is_dir():
        return None
    path = run_dir / "retrievals.json"
    existing: list[Any] = []
    if path.is_file():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            existing = []
    if not isinstance(existing, list):
        existing = []
    existing.append(result)
    path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)
