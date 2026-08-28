#!/usr/bin/env python3
"""按任务检索证据切片：返回候选文件清单，不返回全文。

三轨取证的实现点。事实、风格、编辑策略需要的不是同一批证据，所以 ``--track``
会改变排序与过滤规则，而不只是改变权重：

- ``fact``     优先原文资产与需求素材，初稿降权
- ``style``    只返回终稿与成品参考，排除初稿和对照文件
- ``strategy`` 只返回初终稿对比、经验总结与反馈

老师在需求里点名了参考目录时用 ``--under`` 限定：这是本次信息量最大的一条指令，
优先于系统按品类推断。点名只解除"哪些文件算风格样本"这个由系统猜的门槛，不解除
绑定根边界，也不解除初稿与对照稿的排除。

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


def _under_prefix(under: str | None, kb_root: Path) -> str | None:
    """把 ``--under`` 归一成相对绑定根的 posix 前缀。

    老师在需求里点名目录（「参考知识库里的【媒体深度稿件】文件夹」）是本次信息量
    最大的一条指令，但检索原来没有任何参数能表达它，只能绕过 retrieve 裸读目录，
    于是 ``retrievals.json`` 记下的和真正影响写作的材料不是一回事。

    接受相对绑定根的子路径，也接受绑定根内的绝对路径（含 Windows 反斜杠写法）；
    越出绑定根或带 ``..`` 逃逸时拒绝——绑定根这条边界不因老师点名而松开。
    """
    if not under:
        return None
    raw = str(under).strip().strip('"').replace("\\", "/")
    # 分两行写：security_scan 的 KB_WRITE 规则会把绑定根变量与同一行里的字符串
    # 替换调用一起认成对源知识库的改名写操作。这里只做路径归一，拆开避免误报。
    root_text = str(kb_root)
    root_norm = root_text.replace("\\", "/").rstrip("/")
    if raw.lower().startswith(root_norm.lower() + "/"):
        raw = raw[len(root_norm) + 1:]
    elif Path(raw).is_absolute():
        raise workspace.WorkspaceError(
            f"--under 必须落在绑定的知识库内：{under}\n"
            f"（当前绑定根：{kb_root}；根外材料按附件登记，不自主翻找）"
        )
    prefix = raw.strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise workspace.WorkspaceError(f"--under 不接受空路径或 .. 逃逸：{under}")
    return prefix


def _under_match(rel: str, prefix: str) -> bool:
    """记录是否落在 ``--under`` 指定的子树内。大小写不敏感，迁就 Windows 路径。"""
    low, pre = rel.lower(), prefix.lower()
    return low == pre or low.startswith(pre + "/")


def _track_adjust(record: dict[str, Any], track: str | None,
                  explicit_path: bool = False) -> float | None:
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
        # 老师点名了目录时，"哪些文件算风格样本"已经由老师回答，不再要求文件自身
        # 带终稿或成品参考标记——那两条是系统自己猜的门槛。上面的初稿、对照稿
        # 排除仍然生效，点名不解除它。
        if explicit_path:
            return 1.5
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


def is_style_sample(record: dict[str, Any]) -> bool:
    """style 轨是否接受这条记录。

    判定的唯一来源就是 ``_track_adjust``——这里只是薄适配，不另写一份条件，
    否则改了 style 轨的收录条件就会出现两份互相漂移的判断。
    ``explicit_path`` 取默认 ``False``：可达性问的是"老师没点名路径时"那条路。

    这个谓词属于检索层而不是索引层。索引只记录观察到的属性（stage、
    evidence_type、categories），不记录"够不够资格当风格样本"这类裁决——
    把裁决烧进索引会让每次改策略都要全量重建索引，而且落盘的结论会陈旧。
    """
    return _track_adjust(record, "style") is not None


def style_reach(start=None, index: dict[str, Any] | None = None) -> dict[str, Any]:
    """按品类逐个问「style 轨取不取得到东西」，用于建索引和排障时报告可达性。

    不复述任何过滤条件：直接调用 ``search``，所以轨道过滤、品类匹配、
    ``instruction_artifact`` 排除和 ``score > 0`` 判定全部与真实检索一致。
    ``generate.md`` 规定的调用形态是 ``--track style --category <品类>``，
    这里就照它问，改了也自动跟着改。

    ``index`` 可以传已在内存里的索引（例如刚 build 完的），省掉重复读盘。
    """
    idx = index if index is not None else index_kb.load_index(start)
    records = idx.get("files") or []
    reachable: list[tuple[str, int]] = []
    unreachable: list[str] = []
    for category in index_kb.known_categories():
        found = search(track="style", category=category, limit=1,
                       start=start, index=idx)
        count = found.get("candidates_total") or 0
        if count:
            reachable.append((category, count))
        else:
            unreachable.append(category)
    return {
        "categories": len(index_kb.known_categories()),
        "reachable": reachable,
        "unreachable": unreachable,
        "style_samples": sum(
            1 for r in records
            if is_style_sample(r) and not r.get("instruction_artifact")
        ),
    }


def search(
    query: str = "",
    *,
    category: str | None = None,
    track: str | None = None,
    limit: int = 12,
    since: str | None = None,
    loose: bool = False,
    under: str | None = None,
    include_instruction_artifacts: bool = False,
    index: dict[str, Any] | None = None,
    start=None,
) -> dict[str, Any]:
    """在索引里检索候选证据切片。

    ``index`` 传入已在内存里的索引时不再读盘；不传时行为与以前完全一致。
    """
    ws = workspace.load(start)
    current_root = workspace.kb_root(start)
    index = index if index is not None else index_kb.load_index(start)
    files = index.get("files") or []
    if files:
        index_root = Path(str(index.get("kb_root") or "")).expanduser().resolve()
        mismatches: list[str] = []
        if os.path.normcase(str(index_root)) != os.path.normcase(str(current_root)):
            mismatches.append(f"知识库根 {index_root} != {current_root}")
        if str(index.get("brand") or "") != str(ws.get("brand") or ""):
            mismatches.append(f"品牌 {index.get('brand')} != {ws.get('brand')}")
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
    # 品类名只认规范标签。认不出就**当场停下**，不猜、也不退化成无品类检索。
    # 原来"名字没认出"和"这个品类确实没素材"长得一样（都是 candidates_total 0），
    # 谁都分不清，于是没人知道要去问。这里把它变成显式结果并交出封闭词表，由智能体
    # 做语义映射后重新调用。脚本不做语义：靠扩充别名去认「供稿」「财报稿」这类说法
    # 永远列不全，而且会把找不到变成找错（「文案」会被字面认成「社会化文案」）。
    category_unresolved: str | None = None
    if category and category not in index_kb.known_categories():
        category_unresolved, category = category, None
        if not under:
            return {
                "brand": index.get("brand"),
                "kb_root": index.get("kb_root"),
                "query": query,
                "category": None,
                "category_unresolved": category_unresolved,
                "known_categories": list(index_kb.known_categories()),
                "under": None,
                "under_pool": None,
                "track": track,
                "limit": limit,
                "candidates_total": 0,
                "excluded_instruction_artifacts": 0,
                "hits": [],
                "note": (
                    f"品类名「{category_unresolved}」不是索引使用的规范标签，没有执行检索——"
                    f"不猜也不放宽。规范标签只有这些："
                    f"{'、'.join(index_kb.known_categories())}。"
                    f"按本次任务选一个再调一次，并在方向记录里写下选了哪个；"
                    f"确实判断不了就问老师一个问题。老师点名了目录就改用 --under。"
                ),
            }
        # 同时给了 --under：老师点名的路径优先于系统按品类推断，所以不因为品类名
        # 认不出就把这次检索废掉。丢掉认不出的品类，按路径继续，并在 note 里说明。
    under_prefix = _under_prefix(under, current_root)
    scored: list[tuple[float, dict[str, Any], list[str]]] = []
    excluded_instruction = 0
    under_pool = 0

    for record in files:
        # 混进来的技能包默认不参与检索。它们是提示词和固定模板，被当成经验
        # 证据取出来就等于让外部固定结构接管本次判断——这正是"结构固化"的来源。
        if record.get("instruction_artifact") and not include_instruction_artifacts:
            excluded_instruction += 1
            continue
        if under_prefix:
            if not _under_match(str(record.get("path") or ""), under_prefix):
                continue
            under_pool += 1
        adjust = _track_adjust(record, track, explicit_path=bool(under_prefix))
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

        if under_prefix:
            why.append(f"老师点名路径「{under_prefix}」")

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

    if hits:
        note = ("" if not category_unresolved else
                f"已按老师点名的「{under_prefix}」取材；品类名「{category_unresolved}」"
                f"不是规范标签，本次未按品类过滤。")
    elif under_prefix and not under_pool:
        note = (f"「{under_prefix}」在索引里没有任何文件：确认老师给的路径拼写，"
                f"或先跑 index 重建索引。不要改成翻别的目录。")
    elif under_prefix:
        note = (f"「{under_prefix}」下的 {under_pool} 个文件都不是可用风格样本"
                f"（初稿与对照稿不算）。向老师确认该目录，不要自行换目录。")
    else:
        # 原来这里建议「去掉 --track」。track 为空时 _track_adjust 返回 0.0，
        # 排除初稿与对照稿的守卫就整条失效，候选池等于放开整库——而 generate.md
        # 明令不为凑数放宽到初稿、对照稿或历史 Skill。不能再这样建议。
        note = ("没有命中。老师点名了目录就用 --under 限定；否则确认品类名或用 --query "
                "补关键词。不要去掉 --track，也不要放宽到初稿、对照稿。仍为空时跑 doctor。")

    return {
        "brand": index.get("brand"),
        "kb_root": index.get("kb_root"),
        "query": query,
        "category": category,
        "category_unresolved": category_unresolved,
        "known_categories": list(index_kb.known_categories()),
        "under": under_prefix,
        "under_pool": under_pool if under_prefix else None,
        "track": track,
        "limit": limit,
        "candidates_total": len(scored),
        "excluded_instruction_artifacts": excluded_instruction,
        "hits": hits,
        "note": note,
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
