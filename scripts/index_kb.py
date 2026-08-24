#!/usr/bin/env python3
"""旁路增量索引：只读扫描品牌知识库，派生物全部落在项目内。

设计前提是"**不要求文案老师先做一次知识工程**"。所以索引不追求一次分类正确：
低置信度文件同时保留多个候选标签，只有某个模糊项会实际影响当前这一稿时，才由
当前智能体在访谈里问。

四条纪律：

- 源库只读；
- 增量按内容哈希而不按修改时间；
- 索引可删可重建；
- **知识库里的历史技能包不当证据**。`04-经验总结原稿/xxx-writer/` 这类目录里
  躺着的是其它写作技能的提示词和固定模板，它们是"结构固化"的来源文件。把它们
  当经验证据检索出来，等于让外部提示词接管本次判断。判定按**目录**而不是按
  文件名——固定模板不在 `SKILL.md` 里，在它旁边的 `references/*.md` 里。
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import workspace

INDEX_VERSION = 3
SUMMARY_CHARS = 200
MAX_HASH_BYTES = 16 * 1024 * 1024  # 超出部分不参与哈希，避免为大附件反复全量读盘
SKIP_DIRS = {".git", ".svn", "__pycache__", "node_modules", ".blueink", ".obsidian"}
SKIP_NAMES = {".DS_Store", "Thumbs.db", "desktop.ini"}

TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".json", ".yaml", ".yml", ".csv", ".tsv", ".html"}
OFFICE_EXTS = {".docx", ".pptx"}          # 可解包取正文
METADATA_ONLY_EXTS = {".pdf", ".doc", ".xls", ".xlsx", ".ppt",
                      ".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic"}
MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_OFFICE_XML_BYTES = 48 * 1024 * 1024   # 解压后总量上限，防 zip 炸弹
MAX_OFFICE_XML_MEMBERS = 300

# 历史技能包的根标志：目录里出现这些文件，说明它整棵子树都是指令产物而不是语料
SKILL_ROOT_MARKERS = {"skill.md", "agents.md", "claude.md"}

# 证据类型：命中路径或文件名中的任一关键词即算
EVIDENCE_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("初终稿对比", ("初终", "初版vs", "初版 vs", "初稿vs", "对照组", "对照", "对比")),
    ("经验总结", ("经验", "总结", "复盘", "踩坑", "规律")),
    ("成品参考", ("成品", "终版稿件", "终稿汇总", "参考稿", "范例", "样稿")),
    ("需求素材", ("需求", "brief", "简报", "客户提供", "原始素材", "原始资料")),
    ("反馈", ("反馈", "修改意见", "客户意见", "批注", "审核意见")),
    ("原文资产", ("原文", "资产", "资料", "素材", "官方")),
]

STAGE_FINAL = ("终稿", "终版", "定稿", "final")
STAGE_DRAFT = ("初稿", "初版", "draft", "一稿")
STAGE_PAIR = ("对照", "对比", "vs", "ｖｓ", "初终")

# 品类：键是品类名，值是识别关键词
CATEGORY_RULES: dict[str, tuple[str, ...]] = {
    "新闻稿": ("新闻稿", "通稿", "新闻发布", "press release"),
    "媒体观点供稿": ("媒体观点", "观点供稿", "媒体供稿", "深度稿", "媒体深度", "署名文章"),
    "活动邀请函": ("邀请函", "邀请信", "invitation", "邀约"),
    "媒体传播指引": ("传播指引", "撰稿指引", "媒体bf", "传播bf", "指引", "撰稿要求"),
    "核心信息": ("核心信息", "key message", "关键信息", "核心口径"),
    "演讲稿": ("演讲", "讲话", "致辞", "发言稿", "主题分享"),
    "QA": ("qa", "q&a", "问答", "口径问答"),
    "社会化文案": ("社会化", "社媒", "微博", "微信", "朋友圈", "小红书", "抖音", "短文案"),
    "视频脚本": ("视频脚本", "分镜", "tvc", "片子脚本", "短片脚本"),
    "主持人串词": ("串词", "主持人", "主持稿"),
    "活动物料": ("活动物料", "物料", "欢迎信", "议程", "背板", "手卡"),
}

DATE_PATTERN = re.compile(r"(20\d{2})[-./年]?\s?(\d{1,2})[-./月]?\s?(\d{1,2})?")
ENTITY_PATTERN = re.compile(r"[【《\[]([^】》\]]{2,20})[】》\]]|\b([A-Za-z]{1,3}\d{1,3}[A-Za-z]?)\b")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def content_hash(path: Path) -> str:
    """文件内容哈希。超大文件只哈希前 16MB 并混入文件大小。"""
    digest = hashlib.sha256()
    read = 0
    with path.open("rb") as handle:
        while read < MAX_HASH_BYTES:
            chunk = handle.read(min(65536, MAX_HASH_BYTES - read))
            if not chunk:
                break
            digest.update(chunk)
            read += len(chunk)
    size = path.stat().st_size
    if size > MAX_HASH_BYTES:
        digest.update(f"|size={size}".encode("utf-8"))
    return digest.hexdigest()[:32]


def _read_text(path: Path, limit: int = 8000) -> str:
    """读文本开头若干字符。读不出来（编码怪、二进制）返回空串。"""
    try:
        with path.open("r", encoding="utf-8", errors="strict") as handle:
            return handle.read(limit)
    except (UnicodeDecodeError, OSError):
        pass
    try:
        with path.open("r", encoding="gb18030", errors="replace") as handle:
            return handle.read(limit)
    except OSError:
        return ""


def _read_office(path: Path, limit: int = 8000) -> str:
    """从 DOCX / PPTX 里取可见文本。

    这两种格式就是带 XML 的 zip，标准库能解——所以没有理由把它们降级成"只登记
    元数据"。源库里大量交付物是 docx，放弃它们等于放弃一批真实语料。

    解压总量与成员数都设上限：知识库是别人给的，不能假设里面没有畸形压缩包。
    """
    try:
        with zipfile.ZipFile(path) as archive:
            members = [
                info
                for info in archive.infolist()
                if info.filename.endswith(".xml")
                and any(k in info.filename for k in ("document", "slide", "notesSlide"))
            ]
            if (len(members) > MAX_OFFICE_XML_MEMBERS
                    or sum(m.file_size for m in members) > MAX_OFFICE_XML_BYTES):
                return ""
            pieces: list[str] = []
            total = 0
            for member in members:
                try:
                    node = ElementTree.fromstring(archive.read(member))
                except (ElementTree.ParseError, OSError, zipfile.BadZipFile):
                    continue
                for text in node.itertext():
                    text = text.strip()
                    if not text:
                        continue
                    pieces.append(text)
                    total += len(text)
                    if total >= limit:
                        return "\n".join(pieces)[:limit]
            return "\n".join(pieces)[:limit]
    except (OSError, zipfile.BadZipFile, ValueError):
        return ""


def read_body(path: Path) -> tuple[str, str]:
    """返回 (正文片段, 内容状态)。

    状态是给当前智能体看的诚实标记：``metadata_only*`` 的文件**没有被读过正文**，
    不能当成"这份资料已经检索过了"。
    """
    ext = path.suffix.lower()
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return "", "metadata_only_large"
    except OSError:
        return "", "metadata_only_unreadable"
    if ext in TEXT_EXTS:
        body = _read_text(path)
        return body, "text" if body else "metadata_only_unreadable"
    if ext in OFFICE_EXTS:
        body = _read_office(path)
        return body, "text" if body else "metadata_only_unreadable"
    if ext in METADATA_ONLY_EXTS:
        return "", "metadata_only"
    return "", "unsupported"


def skill_roots(root: Path) -> list[Path]:
    """知识库里所有历史技能包的根目录（相对 ``root``）。

    判定标志是目录里有 ``SKILL.md`` / ``AGENTS.md`` / ``CLAUDE.md``。按目录判定
    才拦得住真正有害的那部分：固定模板写在 `references/*.md` 里，只按
    文件名过滤 `SKILL.md` 会把模板原封不动放进检索结果。
    """
    found: list[Path] = []
    for path in root.rglob("*"):
        if path.name.lower() not in SKILL_ROOT_MARKERS or not path.is_file():
            continue
        try:
            found.append(path.parent.relative_to(root))
        except ValueError:
            continue
    return sorted(set(found), key=lambda p: p.as_posix())


def _is_instruction_artifact(rel: str, roots: list[Path]) -> bool:
    """这个文件是否属于某个历史技能包。"""
    posix = rel if rel.endswith("/") else rel
    for skill_root in roots:
        prefix = skill_root.as_posix()
        if prefix in ("", "."):
            # 知识库根本身就是一个技能包目录：只隔离标志文件与常见提示词子目录
            head = posix.split("/")[0].lower()
            if head in SKILL_ROOT_MARKERS or head in {"references", "agents", "commands", "assets"}:
                return True
            continue
        if posix == prefix or posix.startswith(f"{prefix}/"):
            return True
    return False


def _classify_evidence(haystack: str, layout: dict[str, str], rel: str) -> tuple[list[str], float]:
    """判断证据类型。``corpus_layout`` 命中时置信度更高。"""
    for label, folder in (layout or {}).items():
        if folder and str(folder).lower() in rel.lower():
            return [str(label)], 0.85
    hits = [label for label, keys in EVIDENCE_RULES if any(k in haystack for k in keys)]
    if not hits:
        return ["未分类"], 0.2
    if len(hits) == 1:
        return hits, 0.6
    return hits, 0.4


def _classify_stage(name: str, rel: str) -> str:
    """判断稿件阶段。**文件名优先于目录名**——``03-初终稿对比/【终稿】xxx.md``
    是一份可用的风格样本，不能因为父目录叫"初终稿对比"就整体判成对照。
    """
    low_name = name.lower()
    if any(k in low_name for k in STAGE_FINAL):
        return "终稿"
    if any(k in low_name for k in STAGE_DRAFT):
        return "初稿"
    low_path = rel.lower()
    if any(k in low_path for k in STAGE_PAIR):
        return "对照"
    if any(k in low_path for k in STAGE_FINAL):
        return "终稿"
    if any(k in low_path for k in STAGE_DRAFT):
        return "初稿"
    return "未知"


def _classify_categories(name: str, rel: str, body: str) -> tuple[list[str], list[str], float]:
    """返回 (主品类, 候选品类, 置信度)。命中位置越靠近文件名越可信。"""
    def hits_in(text: str) -> list[str]:
        low = text.lower()
        return [cat for cat, keys in CATEGORY_RULES.items() if any(k in low for k in keys)]

    from_name = hits_in(name)
    if from_name:
        conf = 0.75 if len(from_name) == 1 else 0.45
        return from_name[:1] if len(from_name) == 1 else from_name, from_name, conf
    from_path = hits_in(rel)
    if from_path:
        conf = 0.6 if len(from_path) == 1 else 0.4
        return from_path[:1] if len(from_path) == 1 else from_path, from_path, conf
    from_body = hits_in(body[:600])
    if from_body:
        return from_body[:1] if len(from_body) == 1 else from_body, from_body, 0.3
    return [], [], 0.1


def _extract_date(name: str, body: str) -> str | None:
    for text in (name, body[:400]):
        match = DATE_PATTERN.search(text)
        if not match:
            continue
        year, month, day = match.group(1), match.group(2), match.group(3)
        if not month:
            continue
        try:
            m = int(month)
            d = int(day) if day else 1
            if not 1 <= m <= 12 or not 1 <= d <= 31:
                continue
        except ValueError:
            continue
        return f"{year}-{m:02d}-{d:02d}"
    return None


def _extract_title(name: str, body: str) -> str:
    for line in body.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title:
                return title[:80]
    return Path(name).stem[:80]


def _extract_summary(body: str) -> str:
    text = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    text = re.sub(r"[`*>|\-]{2,}", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:SUMMARY_CHARS]


def _extract_entities(title: str, body: str) -> list[str]:
    found: list[str] = []
    for match in ENTITY_PATTERN.finditer(title + " " + body[:400]):
        value = (match.group(1) or match.group(2) or "").strip()
        if value and value.lower() not in {"md", "txt", "doc"} and value not in found:
            found.append(value)
        if len(found) >= 8:
            break
    return found


def _iter_files(root: Path) -> "tuple[list[Path], list[str]]":
    """枚举源库里的文件，返回 (文件列表, 跳过项说明)。

    **不跟随符号链接。** 库里一条指向别的品牌目录的链接就足以让"检索根只有一个"
    这句话失效，而失败是静默的。跳过项要报出来——静默跳过会让人以为那批资料已经
    进过索引了。
    """
    files: list[Path] = []
    skipped: list[str] = []

    def walk(current: Path) -> None:
        try:
            entries = sorted(current.iterdir(), key=lambda p: p.name)
        except OSError as exc:
            skipped.append(f"目录不可读：{current.relative_to(root).as_posix()}（{exc.strerror}）")
            return
        for path in entries:
            name = path.name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                skipped.append(f"符号链接：{rel}（不跟随，避免越过绑定目录）")
                continue
            if path.is_dir():
                if name in SKIP_DIRS or name.startswith("."):
                    continue
                walk(path)
                continue
            if not path.is_file() or name in SKIP_NAMES or name.startswith("."):
                continue
            files.append(path)

    walk(root)
    return files, skipped


def build_record(path: Path, root: Path, layout: dict[str, str],
                 roots: list[Path] | None = None) -> dict[str, Any]:
    """为一个文件生成索引记录。"""
    rel = path.relative_to(root).as_posix()
    stat = path.stat()
    ext = path.suffix.lower()
    body, content_status = read_body(path)
    extractable = content_status == "text"
    haystack = (rel + " " + path.name).lower()
    instruction = _is_instruction_artifact(rel, roots or [])

    evidence_type, ev_conf = _classify_evidence(haystack, layout, rel)
    categories, candidates, cat_conf = _classify_categories(path.name, rel, body)
    title = _extract_title(path.name, body) if extractable else Path(path.name).stem[:80]

    if instruction:
        # 归成独立证据类型，并把品类标签清空：带着六个品类标签的技能包 SKILL.md
        # 会在任何品类检索里高分命中。
        evidence_type = ["历史技能产物"]
        categories, candidates = [], []
        ev_conf, cat_conf = 0.9, 0.9

    return {
        "path": rel,
        "hash": content_hash(path),
        "size": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "ext": ext.lstrip("."),
        "evidence_type": evidence_type,
        "stage": "未知" if instruction else _classify_stage(path.name, rel),
        "categories": categories,
        "candidates": candidates,
        "confidence": round((ev_conf + cat_conf) / 2, 2),
        "date": _extract_date(path.name, body),
        "title": title,
        "summary": _extract_summary(body),
        "extractable": extractable,
        "content_status": content_status,
        "instruction_artifact": instruction,
        "entities": _extract_entities(title, body) if extractable else [],
    }


def load_index(start=None) -> dict[str, Any]:
    """读现有索引；不存在时返回空壳。"""
    path = workspace.index_dir(start) / "index.json"
    if not path.is_file():
        return {"version": INDEX_VERSION, "files": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"version": INDEX_VERSION, "files": []}
    if data.get("version") != INDEX_VERSION:
        # schema 变了就全量重建，而不是混用两代记录
        return {"version": INDEX_VERSION, "files": []}
    return data


def build(full: bool = False, limit: int | None = None, start=None) -> dict[str, Any]:
    """全量或增量重建索引。返回统计信息。

    ``limit`` 是**试跑预览**：只扫前 N 个文件、不落盘。否则一次
    ``index --limit 5`` 会把已有的 186 条索引截成 5 条，而"移除 181"看起来
    像是知识库真的少了文件。
    """
    ws = workspace.load(start)
    root = workspace.kb_root(start)
    layout = ws.get("corpus_layout") or {}
    roots = skill_roots(root)

    previous = {} if full else {rec["path"]: rec for rec in load_index(start).get("files", [])}
    records: list[dict[str, Any]] = []
    added = updated = reused = 0
    scanned = 0

    on_disk, skipped = _iter_files(root)
    for path in on_disk:
        scanned += 1
        if limit and scanned > limit:
            break
        rel = path.relative_to(root).as_posix()
        old = previous.get(rel)
        if old is not None:
            try:
                if old.get("hash") == content_hash(path):
                    records.append(old)
                    reused += 1
                    continue
            except OSError:
                continue
        try:
            records.append(build_record(path, root, layout, roots))
        except OSError:
            continue
        if old is None:
            added += 1
        else:
            updated += 1

    removed = [] if limit else sorted(set(previous) - {rec["path"] for rec in records})

    index = {
        "version": INDEX_VERSION,
        "brand": ws["brand"],
        "brand_key": ws["brand_key"],
        "kb_root": str(root),
        "built_at": _now(),
        "preview": bool(limit),
        "skill_roots": [p.as_posix() for p in roots],
        "skipped": skipped,
        "files": records,
        "stats": {
            "total": len(records),
            "added": added,
            "updated": updated,
            "reused": reused,
            "removed": len(removed),
            "extractable": sum(1 for r in records if r.get("extractable")),
            "metadata_only": sum(1 for r in records if not r.get("extractable")),
            "low_confidence": sum(1 for r in records if (r.get("confidence") or 0) < 0.5),
            "uncategorized": sum(
                1 for r in records if not r.get("categories") and not r.get("instruction_artifact")
            ),
            "instruction_artifacts": sum(1 for r in records if r.get("instruction_artifact")),
            "skipped": len(skipped),
        },
        "removed_paths": removed,
    }

    if limit:
        return index

    out_dir = workspace.index_dir(start)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cache").mkdir(exist_ok=True)
    (out_dir / "index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return index


def freshness(start=None) -> dict[str, Any]:
    """索引与源库的差异，供 doctor 使用。不重建索引。"""
    index = load_index(start)
    if not index.get("files"):
        return {"indexed": 0, "changed": None, "note": "尚未建立索引"}
    try:
        root = workspace.kb_root(start)
    except workspace.WorkspaceError as exc:
        return {"indexed": len(index["files"]), "changed": None, "note": str(exc)}

    known = {rec["path"]: rec for rec in index["files"]}
    on_disk_paths, skipped = _iter_files(root)
    on_disk = {p.relative_to(root).as_posix() for p in on_disk_paths}
    new_paths = sorted(on_disk - set(known))
    gone = sorted(set(known) - on_disk)
    return {
        "indexed": len(known),
        "on_disk": len(on_disk),
        "new": len(new_paths),
        "missing": len(gone),
        "changed": len(new_paths) + len(gone),
        "skipped": len(skipped),
        "sample_new": new_paths[:5],
        "sample_missing": gone[:5],
        "sample_skipped": skipped[:5],
        "built_at": index.get("built_at"),
    }
