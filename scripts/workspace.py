#!/usr/bin/env python3
"""单品牌单老师工作空间：绑定、读取与路径归属判断。

跨品牌污染不靠提示词防，靠**检索根只有一个**。这个模块就是那个唯一的根的
来源：`.blueink/workspace.yaml` 记录本项目绑定的品牌、负责这个品牌的文案老师
与知识库目录，之后所有检索都只从这个根出发。

**为什么要记老师是谁。** 品牌隔离挡不住个人偏好互相污染：同一个品牌可能由两位
老师分别负责不同稿件，条件化记忆学的是"这位老师在什么条件下通常如何判断"，混在
一起就学成了一个不存在的平均人。所以工作空间同时锁品牌和锁人——换品牌或换人都
要换项目。
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import miniyaml
import official

BLUEINK_DIR = ".blueink"
WORKSPACE_FILE = "workspace.yaml"
SCHEMA_VERSION = 2

# 常见文本格式：绑定时用来判断这个目录里到底有没有可用语料
TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".json", ".yaml", ".yml", ".csv"}

# 语料布局的识别关键词：出现在 kb_root 的直接子目录名里，说明绑到了品牌那一层
CORPUS_HINTS = ("原文", "初终", "初版", "终版", "终稿", "经验", "成品", "需求", "反馈",
                "稿件", "新闻", "对照", "对比", "资产", "资料")

# 新建知识库时的标准骨架。五个目录对应五类证据，名字里带 CORPUS_HINTS 的特征词，
# 因此新建出来的目录不会被 looks_like_brand_collection 误判成品牌集合层。
DEFAULT_CORPUS_LAYOUT: dict[str, str] = {
    "原文资产": "01-原文资产",
    "需求素材": "02-需求素材",
    "初终稿对比": "03-初终稿对比",
    "经验总结": "04-经验总结",
    "成品参考": "05-成品参考",
}

# 品牌短标识。中文转拼音不在标准库里，所以常见的直接给，其余按规则推导。
KNOWN_BRAND_KEYS = {
    "理想汽车": "lixiang",
    "理想": "lixiang",
    "东风奕派": "dongfeng-yipai",
    "奕派": "dongfeng-yipai",
    "现代汽车": "hyundai",
    "现代中国": "hyundai-china",
    "现代N品牌": "hyundai-n",
    "现代 N 品牌": "hyundai-n",
    "千里科技": "qianli",
    "千里": "qianli",
}


class WorkspaceError(RuntimeError):
    """工作空间未绑定、绑定信息不完整，或绑定目标已失效。"""


# --- 定位 -------------------------------------------------------------------


def find_project_root(start: str | os.PathLike[str] | None = None) -> Path | None:
    """从 ``start``（默认当前目录）向上找含 ``.blueink/`` 的目录。

    找不到返回 ``None``。向上查找在文件系统根处停止。
    """
    cur = Path(start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / BLUEINK_DIR).is_dir():
            return candidate
    return None


def project_root(start: str | os.PathLike[str] | None = None) -> Path:
    """已绑定项目的根目录；未绑定时用 ``start``（默认当前目录）本身。"""
    return find_project_root(start) or Path(start or Path.cwd()).resolve()


def blueink_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return project_root(start) / BLUEINK_DIR


def workspace_file(start: str | os.PathLike[str] | None = None) -> Path:
    return blueink_dir(start) / WORKSPACE_FILE


def index_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return blueink_dir(start) / "index"


def learning_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return blueink_dir(start) / "learning"


def runs_dir(start: str | os.PathLike[str] | None = None) -> Path:
    return blueink_dir(start) / "runs"


# --- 读 ---------------------------------------------------------------------


def is_bound(start: str | os.PathLike[str] | None = None) -> bool:
    return workspace_file(start).is_file()


def load(start: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """读取并校验 ``workspace.yaml``。

    Raises:
        WorkspaceError: 未绑定、文件损坏，或缺少 ``brand`` / ``kb_root``。
    """
    path = workspace_file(start)
    if not path.is_file():
        raise WorkspaceError(
            f"当前项目未绑定品牌工作空间（找不到 {BLUEINK_DIR}/{WORKSPACE_FILE}）。"
            f"先运行：python3 scripts/blueink.py bind --brand <品牌> --kb <知识库目录>"
        )
    try:
        data = miniyaml.load_file(path)
    except miniyaml.YamlError as exc:
        raise WorkspaceError(f"{path} 解析失败：{exc}") from exc
    if not isinstance(data, dict):
        raise WorkspaceError(f"{path} 内容不是键值结构")
    for field in ("brand", "kb_root"):
        if not data.get(field):
            raise WorkspaceError(f"{path} 缺少必填字段 {field}")
    data.setdefault("version", SCHEMA_VERSION)
    data.setdefault("brand_key", derive_brand_key(str(data["brand"])))
    # teacher 是 v2 新增。v1 的工作空间照旧能读，但记忆归属会标成"未记名"，
    # doctor 会提示重新绑定——不因为一个新字段就让老工作空间失效。
    data.setdefault("teacher", "")
    data.setdefault("official_sources", [])
    data.setdefault("corpus_layout", {})
    data.setdefault("notes", "")
    if data.get("official_sources") is None:
        data["official_sources"] = []
    if data.get("corpus_layout") is None:
        data["corpus_layout"] = {}
    return data


def bound_teacher(start: str | os.PathLike[str] | None = None) -> str:
    """当前工作空间登记的文案老师。v1 老工作空间返回空串。"""
    return str(load(start).get("teacher") or "")


def kb_root(start: str | os.PathLike[str] | None = None) -> Path:
    """当前绑定的知识库根目录。目录已失效时抛 ``WorkspaceError``。"""
    data = load(start)
    root = Path(str(data["kb_root"]))
    if not root.is_dir():
        raise WorkspaceError(
            f"绑定的知识库目录已不存在或不可读：{root}\n"
            f"知识库改名、移动或换电脑后需要重新绑定（bind --force）。"
        )
    return root.resolve()


# --- 路径归属 ---------------------------------------------------------------


def within(path: str | os.PathLike[str], root: str | os.PathLike[str]) -> bool:
    """``path`` 是否落在 ``root`` 之内（软链接按真实路径判断）。

    这是"检索不越过绑定目录"的唯一实现点。审计器和检索都调用它，不各写一份。
    """
    try:
        target = Path(path).expanduser().resolve()
        base = Path(root).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return target == base or base in target.parents


# --- 品牌归属 ---------------------------------------------------------------


def normalize_brand(brand: str) -> str:
    """品牌名归一化：去空格、统一小写。**不去「汽车／科技」这类后缀**——去掉它
    会把「理想汽车」和「理想科技」判成同一个品牌，那正好是要防的方向。
    """
    return re.sub(r"\s+", "", str(brand or "")).lower()


def brand_matches(data: dict[str, Any], brand: str) -> tuple[bool, str]:
    """本次任务要写的品牌，与这个工作空间绑定的品牌是不是同一个。

    这是"品牌与知识库是否匹配"的唯一判定点。``open`` 和 ``check-brand`` 都调用它。

    Returns:
        (是否匹配, 一句话说明)。不匹配时说明里写清双方是谁，供上层组织提示语。

    判据三条，任一命中即匹配：绑定品牌与本次品牌归一化后相等；``brand_key`` 相等；
    一方是另一方的子串（老师说「理想」而绑定写的是「理想汽车」）。

    子串这一条是有意放宽的：老师在访谈里习惯说简称，把简称判成不匹配会让他每次
    都要回答一次"是不是要换知识库"，而那个提示很快就会被当成噪音跳过。反过来，
    「现代中国」与「现代汽车」互不为子串，仍然会被判为不匹配——这正是需要拦的那类。
    """
    bound = str(data.get("brand") or "")
    asked = str(brand or "")
    if not asked.strip():
        return True, "本次没有声明品牌，按绑定品牌处理"
    left, right = normalize_brand(bound), normalize_brand(asked)
    if not left:
        return False, "工作空间没有登记品牌"
    if left == right:
        return True, f"品牌一致：{bound}"
    bound_key = normalize_brand(str(data.get("brand_key") or ""))
    if bound_key and bound_key == normalize_brand(derive_brand_key(asked)):
        return True, f"品牌标识一致：{data.get('brand_key')}"
    if left in right or right in left:
        return True, f"本次说的「{asked}」与绑定的「{bound}」是同一个品牌的不同叫法"
    return False, f"本次要写「{asked}」，而这个工作空间绑定的是「{bound}」"



# --- 绑定 -------------------------------------------------------------------


def derive_brand_key(brand: str) -> str:
    """给品牌名取一个只含 ASCII 的短标识，用于文件名与索引。"""
    brand = brand.strip()
    if brand in KNOWN_BRAND_KEYS:
        return KNOWN_BRAND_KEYS[brand]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", brand.lower()).strip("-")
    if ascii_part:
        return ascii_part
    digest = hashlib.md5(brand.encode("utf-8")).hexdigest()[:6]
    return f"brand-{digest}"


def _count_text_files(root: Path, cap: int = 200) -> int:
    """粗略统计可读文本文件数量，命中 ``cap`` 即停。"""
    seen = 0
    for path in root.rglob("*"):
        name = path.name
        if name.startswith(".") or not path.is_file():
            continue
        if path.suffix.lower() in TEXT_EXTS:
            seen += 1
            if seen >= cap:
                break
    return seen


def looks_like_brand_collection(root: Path) -> list[str]:
    """判断是否绑到了"品牌集合层"而不是某个具体品牌。

    返回疑似品牌子目录名列表；判定为正常绑定时返回空列表。

    符号链接不计入。索引本来就不跟随符号链接，把它算成兄弟品牌会让一条无关的
    链接凭空触发拦截——而误拦比漏拦更糟：它会逼人习惯性加 --force。
    """
    children = [
        p for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".") and not p.is_symlink()
    ]
    if len(children) < 2:
        return []
    has_corpus_dir = any(
        any(hint in child.name for hint in CORPUS_HINTS) for child in children
    )
    if has_corpus_dir:
        return []
    return sorted(child.name for child in children)


def create_kb_skeleton(root: Path, brand: str) -> list[str]:
    """按标准语料布局创建一个空的品牌知识库骨架。

    存在的理由是：老师第一次用这个技能时，手上常常只有散落在各处的稿件，还没有
    "一个品牌知识库目录"。要求他先自己建目录、自己分类，等于把知识工程的活推回给
    他——而"不要求老师先整理知识库"是这套设计的前提之一。

    Returns:
        创建出来的目录相对路径列表（已存在的目录不重复报）。

    骨架只建目录和一份说明，**不放任何品牌内容**。放进去的第一份文件应该是老师
    自己的稿件，由他决定放哪一类；分不清就随便放，索引会给出候选标签。
    """
    created: list[str] = []
    root.mkdir(parents=True, exist_ok=True)
    for label, folder in DEFAULT_CORPUS_LAYOUT.items():
        target = root / folder
        if not target.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            created.append(f"{folder}/（{label}）")
    readme = root / "README.md"
    if not readme.is_file():
        readme.write_text(
            f"# {brand} 知识库\n\n"
            f"这个目录是 {brand} 的单品牌语料库。BlueInk 只读它，永不改写、"
            f"重命名或移动这里的任何文件。\n\n"
            "## 放什么\n\n"
            + "".join(
                f"- `{folder}/`：{label}\n" for label, folder in DEFAULT_CORPUS_LAYOUT.items()
            )
            + "\n## 三件不需要做的事\n\n"
            "- **不需要先分好类。** 分不清放哪一类就随便放，索引会给出多个候选标签，"
            "只有某个模糊项真的会影响某一稿时才会问你。\n"
            "- **不需要改文件名。** 文件名里的日期、品类和「初稿／终稿」字样都会被自动识别，"
            "改名反而会丢掉这些线索。\n"
            "- **不需要清理旧文件。** 过期资料保留即可，证据带时效，取证时按日期取近的那份。\n\n"
            "## 只有一件事必须做\n\n"
            "**这个目录里只放这一个品牌的材料。** 跨品牌隔离靠"
            "「检索根只有一个」实现——放进别家客户的文件，隔离就失效了。\n",
            encoding="utf-8",
        )
        created.append("README.md（这个目录放什么、不需要做什么）")
    return created


def bind(
    brand: str,
    kb: str | os.PathLike[str],
    *,
    teacher: str = "",
    brand_key: str | None = None,
    official_urls: list[str] | None = None,
    corpus_layout: dict[str, str] | None = None,
    notes: str = "",
    force: bool = False,
    create: bool = False,
    start: str | os.PathLike[str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """把品牌、文案老师与知识库目录写进当前项目的 ``.blueink/workspace.yaml``。

    Args:
        create: 目录不存在时按标准语料布局建出来，而不是报错。老师手上还没有
            知识库目录时走这条路；此时空目录不再是拒绝理由。

    Returns:
        (写入的配置, 需要提示给用户的告警列表)

    Raises:
        WorkspaceError: 目录不存在（且未给 ``create``）、目录里没有可读文本、绑到了
            品牌集合层、官方域名格式无效，或已绑定其它品牌／老师／知识库而未加 ``force``。
    """
    warnings: list[str] = []
    root = Path(kb).expanduser()
    just_created: list[str] = []
    if not root.is_dir():
        if not create:
            raise WorkspaceError(
                f"知识库目录不存在或不是目录：{root}\n"
                f"这个品牌还没有知识库目录时，加 --create 让它按标准语料布局建出来。"
            )
        if root.exists():
            raise WorkspaceError(f"这个路径已存在但不是目录，无法作为知识库：{root}")
        just_created = create_kb_skeleton(root, brand.strip())
    root = root.resolve()

    text_files = _count_text_files(root)
    if text_files == 0 and not (force or just_created):
        raise WorkspaceError(
            f"{root} 下没有找到任何可读文本文件（{'/'.join(sorted(TEXT_EXTS))}）。"
            f"检索会永远返回空。确认目录正确后可用 --force 强制绑定。"
        )
    if text_files == 0 and not just_created:
        warnings.append("目录内没有可读文本文件，检索将返回空结果。")
    if just_created:
        warnings.append(
            "新建了知识库骨架：" + "、".join(just_created)
            + "。目录现在是空的，检索会返回空——把这个品牌的稿件放进去再跑一次 index。"
            "分不清放哪一类就随便放，索引会给候选标签。"
        )


    # 绑到品牌集合层是**拦截**而不是告警。告警会被忽略，而它的后果是整套稿子
    # 混用两套品牌表达——这正是本技能要消灭的那类静默失败。
    collection = looks_like_brand_collection(root)
    if collection and not force:
        raise WorkspaceError(
            "这个目录看起来是品牌集合层而不是单个品牌，子目录："
            + "、".join(collection[:6])
            + "。一个工作空间只能绑一个品牌，绑到集合层等于同时打开多套调性，"
              "同一稿会混用不同品牌的表达。请绑到具体品牌那一级"
              "（现代汽车这类双品牌客户要绑到 现代中国 / 现代N品牌 这一层）。"
              "确认这个目录就是单一品牌请加 --force。"
        )
    if collection:
        warnings.append(
            "已按 --force 绑到疑似品牌集合层：" + "、".join(collection[:6])
            + "。跨品牌污染检查会因此失效，请自行确认目录单一。"
        )

    if not str(teacher).strip():
        warnings.append(
            "没有登记文案老师（--teacher）。条件化记忆会标成「未记名」，"
            "这个项目换人使用时无法区分谁的偏好。建议重新绑定并写上负责人。"
        )

    # 官方域名在写入时就校验一次，而不是等检索时才发现格式不对
    normalized: list[dict[str, str]] = []
    for url in official_urls or []:
        try:
            host, note = official.split_source(url)
        except official.OfficialSourceError as exc:
            raise WorkspaceError(f"官方来源无效：{exc}") from exc
        if note:
            warnings.append(
                f"官方来源 {url}：{note}。白名单是主机级的，实际生效的是 {host} 及其子域。"
            )
        if any(item["url"] == f"https://{host}" for item in normalized):
            continue
        normalized.append({"name": host, "url": f"https://{host}"})

    base = Path(start or Path.cwd()).resolve()
    # 绑定目标就是 --project／当前目录本身，**不向上继承父目录的工作空间**。
    # 向上写会把"在子目录里绑定"变成"悄悄改了父项目的绑定"。
    outer = find_project_root(base.parent) if base.parent != base else None
    if outer is not None:
        warnings.append(
            f"上层目录 {outer} 已有一个工作空间。本次绑定只作用于 {base}，"
            f"两者互不影响；如果你本意是改那一个，请在那个目录下执行 bind。"
        )
    target = base / BLUEINK_DIR / WORKSPACE_FILE
    if target.is_file() and not force:
        existing = miniyaml.load_file(target)
        if isinstance(existing, dict):
            # 品牌、老师、知识库三者任一改变都要显式确认：改绑会让现有索引与
            # 记忆的归属失真，而失真是看不出来的。
            changes = [
                (label, old, new)
                for label, old, new in (
                    ("品牌", existing.get("brand"), brand.strip()),
                    ("文案老师", existing.get("teacher"), str(teacher).strip()),
                    ("知识库", existing.get("kb_root"), str(root)),
                )
                if old not in (None, "", new)
            ]
            if changes:
                detail = "；".join(f"{label} 由「{old}」改为「{new}」" for label, old, new in changes)
                raise WorkspaceError(
                    f"当前项目已绑定，本次会改变：{detail}。"
                    f"一个工作空间只服务一个品牌和一位老师——换品牌或换人请新建项目目录。"
                    f"确实要在这个项目里改绑请加 --force —— 这会作废现有索引，"
                    f"并让已有记忆的归属不再准确。"
                )

    data: dict[str, Any] = {
        "version": SCHEMA_VERSION,
        "brand": brand.strip(),
        "brand_key": (brand_key or derive_brand_key(brand)).strip(),
        "teacher": str(teacher).strip(),
        "kb_root": str(root),
        "bound_at": date.today().isoformat(),
        "official_sources": normalized,
        # 新建骨架时布局是已知的，直接写进去：填了布局的索引比靠文件名猜更准。
        "corpus_layout": corpus_layout or (dict(DEFAULT_CORPUS_LAYOUT) if just_created else {}),
        "notes": notes,
    }

    target.parent.mkdir(parents=True, exist_ok=True)
    for sub in ("index", "learning", "runs"):
        (base / BLUEINK_DIR / sub).mkdir(parents=True, exist_ok=True)
    miniyaml.dump_file(target, data)
    return data, warnings


def official_hosts(data: dict[str, Any]) -> list[str]:
    """白名单里的主机名。判定逻辑在 ``official`` 模块里，这里只是转发。"""
    return official.hosts(data)
