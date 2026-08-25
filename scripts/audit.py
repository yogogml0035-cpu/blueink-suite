#!/usr/bin/env python3
"""五项验收契约的机械审计器。

这是"出问题不知道问题出在哪个 md"的直接解法：把一次运行的调用轨迹喂进来，它
告诉你这次运行在哪一条契约上违约，以及证据在哪个文件的哪个字段。

它只读运行记录目录，不依赖当前项目是否绑定——所以同一套检查既能审真实运行，也
能审 ``evals/golden/`` 里的构造用例。

三种结论：

- ``pass``       五项全部通过。**不代表稿子好**，只代表流程没有违约。
- ``violated``   至少一项违约。看 ``failed`` 与对应 ``detail``。
- ``incomplete`` 运行没跑完，有回执缺失。运行中途中断很常见，不是 bug。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import run_record
import workspace

ENTRY = "/blueink-suite"
ENTRY_ALIASES = (ENTRY, "/blueink-suite:blueink-suite")
STAGE_ID_BY_FILE = {
    "evidence.json": "evidence-researcher",
    "strategy.json": "editorial-strategist",
    "strategy-b.json": "editorial-strategist",
    "write-receipt.json": "professional-writer",
    "write-receipt-a.json": "professional-writer",
    "write-receipt-b.json": "professional-writer",
    "verify.json": "source-verifier",
    "verify-a.json": "source-verifier",
    "verify-b.json": "source-verifier",
    "adversary.json": "editorial-adversary",
    "feedback.json": "feedback-attributor",
}

REQUIRED_BY_MODE = {
    "生成": [
        ("interview.json",),
        ("evidence.json",),
        ("strategy.json",),
        ("program.json",),
        ("draft.md", "draft-a.md"),
        ("write-receipt.json", "write-receipt-a.json"),
        ("verify.json", "verify-a.json"),
        ("adversary.json",),
        ("delivery.md",),
    ],
    "学习": [("feedback.json",)],
    "绑定": [],
    "定位": [],
}

VERDICTS = ("可进入人工初审", "有待确认项", "暂不建议提交")

# 出现在核验／红队回执里就说明该阶段越界改稿
REWRITE_KEYS = {
    "rewritten", "revised", "revised_draft", "new_text", "fixed_text",
    "suggested_sentence", "suggested_text", "replacement", "rewrite",
}

QUESTION_MARKS = re.compile(r"[？?]")
# 引号内的问句是被引述的内容，不是这一轮问出去的问题。不剥掉的话
# 「我打算问客户「这个数字能对外吗？」，还是你直接告诉我？」会被误判成一次多问，
# 而审计器一旦开始误报，人就会学会忽略它。
QUOTED_SPAN = re.compile(r"「[^」]*」|『[^』]*』|“[^”]*”|‘[^’]*’|\"[^\"]*\"|'[^']*'")

# 动态充分性的五个维度。**这是这五个维度的唯一定义点**：访谈协议里的措辞、
# 停止理由的判定、以及 A3 报出的覆盖情况都取自这里，不各写一份。
#
# 每个维度给一组同义说法，因为老师和主智能体不会照抄术语——停止理由写"邀请函无法
# 成立"表达的就是交付可行性。判定要认这类自然说法，否则会训练出"把五个词都抄一遍"
# 的行为，而那样的停止理由同样什么都没约束住。
SUFFICIENCY_DIMENSIONS: dict[str, tuple[str, ...]] = {
    "事实安全": ("事实", "来源", "口径", "时效", "真实性", "查证", "冲突"),
    "传播主线": ("主线", "传播方向", "方向", "主张", "切口", "核心信息"),
    "信息权重": ("信息权重", "权重", "笔墨", "取舍", "信息预算", "重心", "详略"),
    "表达边界": ("表达边界", "表达", "品牌表达", "语义温度", "发言人", "调性",
                 "身份", "口吻", "人称"),
    "交付可行性": ("交付可行性", "交付", "可行", "篇幅", "字数", "形态", "格式",
                   "成立", "交付合同"),
}

# 伪精确的理解信心数值。匹配"信心 87%""confidence: 0.92""把握 95%"这类写法。
# 不匹配置信度记忆里的 confidence——那是有明确算术定义的排序信号，两者不是一回事，
# 所以这里只认与「信心／把握／理解」同现的数值。
_FAKE_CONFIDENCE = re.compile(
    r"(?:信心|把握|理解程度|理解度)\s*[:：]?\s*\d{1,3}\s*%"
    r"|(?:信心|把握|理解程度|理解度)\s*[:：]?\s*0?\.\d+"
    r"|(?:confidence|certainty)\s*[:：]?\s*\d{1,3}\s*%"
)


def sufficiency_dimensions(text: str) -> list[str]:
    """这段停止理由指名评估过哪几个动态充分性维度。

    返回命中的维度名（按 ``SUFFICIENCY_DIMENSIONS`` 的顺序）。空列表意味着这段
    理由没有说它评估的是什么——那对任何一次访谈都成立，因此不构成一个停止判断。
    """
    blob = str(text or "")
    return [
        name for name, keys in SUFFICIENCY_DIMENSIONS.items()
        if any(k in blob for k in keys)
    ]


def _normalize_question(text: str) -> str:
    """把一个问题归一化到可比对的形态，用于发现同一个问题被问了两遍。

    只做空白与标点归一化，**不做语义相似度**：语义判断会把"同一话题的不同追问"
    误判成重复，而那类追问恰恰是访谈该做的事。
    """
    stripped = re.sub(r"[\s，。、；：？！?!,.;:（）()「」『』\"'“”‘’—\-]+", "", str(text or ""))
    return stripped.lower()


def _check_stop_reason(check: "Check", stopped: str, *, zero_round: bool) -> None:
    """停止理由必须指名它评估过哪一个动态充分性维度。

    不指名的理由（"信息已经足够""没什么要问的"）对任何一次访谈都成立，因此它
    什么都没约束住——而一条约束不住任何东西的检查项，比没有这一项更糟：它会让
    人以为访谈的停止时机已经被管住了。
    """
    named = sufficiency_dimensions(stopped)
    prefix = "本次零轮访谈" if zero_round else "访谈停止"
    if not named:
        check.fail(
            f"{prefix}，但 stopped_because 没有指名评估过哪一个维度"
            f"（{'／'.join(SUFFICIENCY_DIMENSIONS)}）——"
            f"一句不指名的「够了」对任何访谈都成立：{stopped[:40]}",
            "interview.json:stopped_because",
        )
        return
    check.note(f"{prefix}，覆盖维度：{'、'.join(named)}｜理由：{stopped[:50]}")


def _check_fake_confidence(check: "Check", data: Any) -> None:
    """访谈记录里不许出现伪精确的理解信心数值。

    "当前信心 87%" 无法校准，但它看起来像一次测量，老师会照着它决定要不要再补
    材料。动态充分性的判断标准是行为——下一问还可不可能改变这一稿——不是数字。
    """
    hit = _FAKE_CONFIDENCE.search(json.dumps(data, ensure_ascii=False))
    if hit:
        check.fail(
            f"出现伪精确的理解信心数值：{hit.group(0)}"
            f"（动态充分性的判断标准是行为，不是无法校准的百分比）",
            "interview.json",
        )


def _question_count(text: str) -> int:
    """这一轮实际问出去的问题数。"""
    return len(QUESTION_MARKS.findall(QUOTED_SPAN.sub("", text)))


class Check:
    """一条契约的检查结果。"""

    def __init__(self, cid: str, name: str) -> None:
        self.id = cid
        self.name = name
        self.status = "pass"
        self.details: list[str] = []
        self.skips: list[str] = []
        self.evidence: list[str] = []

    def fail(self, detail: str, evidence: str = "") -> None:
        self.status = "fail"
        self.details.append(detail)
        if evidence:
            self.evidence.append(evidence)

    def skip(self, detail: str) -> None:
        """记一条"这部分本次没走到"。

        只有整条检查都没内容可查时才呈现为 skip。同一条契约里"某一半跳过、另一半
        违约"是常态（例如本次没有反馈但记忆库本身有问题）——那时结论必须是违约，
        不能让"未启动"这句话把违约冲淡。
        """
        self.skips.append(detail)
        if self.status == "pass":
            self.status = "skip"

    def note(self, detail: str) -> None:
        self.details.append(detail)

    def as_dict(self) -> dict[str, Any]:
        if self.status == "fail":
            shown = self.details
        else:
            shown = self.details + self.skips
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "detail": "；".join(shown) if shown else "通过",
            "evidence": self.evidence,
        }


def _load(run_dir: Path, name: str) -> tuple[Any, str | None]:
    """读运行记录里的一个 JSON。返回 (数据, 错误说明)。文件不存在返回 (None, None)。"""
    path = run_dir / name
    if not path.is_file():
        return None, None
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, f"{name} 不是合法 JSON：{exc}"
    except OSError as exc:
        return None, f"{name} 读取失败：{exc}"


def _text(run_dir: Path, name: str) -> str | None:
    path = run_dir / name
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _walk_strings(node: Any, key_filter: set[str] | None = None, path: str = "") -> list[tuple[str, Any]]:
    """深度遍历，返回 (键路径, 值)。``key_filter`` 非空时只返回命中的键。"""
    out: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if key_filter is None or str(key).lower() in key_filter:
                out.append((here, value))
            out.extend(_walk_strings(value, key_filter, here))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out.extend(_walk_strings(value, key_filter, f"{path}[{i}]"))
    return out


def _collect_read_paths(run_dir: Path, include_retrievals: bool = True) -> list[tuple[str, str]]:
    """收集各回执里声明读过的路径。返回 (来源文件:字段, 路径)。

    ``include_retrievals`` 决定是否把 ``retrievals.json`` 的候选命中算进来。这两种
    用法必须分开，否则会开出一个静默漏洞：

    - **A2 要算**。检索候选也必须落在绑定根内——一次返回了根外路径的检索本身就是
      隔离失效，哪怕没人去读那个文件。
    - **A5 不能算**。候选清单是"检索返回了什么"，不是"谁打开了什么"（``retrieve``
      刻意不返回全文）。把候选算成已读，等于允许来源清单引用一份从没被打开过的
      文件——那和凭空写出的引用是同一类问题，而它是本技能存在的理由之一。
    """
    found: list[tuple[str, str]] = []
    for name in ("evidence.json", "strategy.json", "strategy-b.json", "verify.json",
                 "verify-a.json", "verify-b.json", "write-receipt.json",
                 "write-receipt-a.json", "write-receipt-b.json", "adversary.json"):
        data, _ = _load(run_dir, name)
        if not isinstance(data, dict):
            continue
        for value in data.get("read_paths") or []:
            if isinstance(value, str) and value:
                found.append((f"{name}:read_paths", value))
        for value in data.get("style_refs_used") or []:
            if isinstance(value, str) and value:
                found.append((f"{name}:style_refs_used", value))
    if not include_retrievals:
        return found
    retrievals, _ = _load(run_dir, "retrievals.json")
    if isinstance(retrievals, list):
        for i, result in enumerate(retrievals):
            if not isinstance(result, dict):
                continue
            for hit in result.get("hits") or []:
                if isinstance(hit, dict) and isinstance(hit.get("path"), str):
                    found.append((f"retrievals.json[{i}]:hits.path", hit["path"]))
    return found


def _drafts(run_dir: Path) -> list[str]:
    """已成稿的正文文件。用来区分"还没走到这一步"与"走到了却违约"。"""
    return [n for n in ("draft.md", "draft-a.md", "draft-b.md") if (run_dir / n).is_file()]


def _archived(meta: Any) -> bool:
    """这次运行是否已经归档（``close`` 过）。

    "下游产物缺失"这一类判定必须以它为条件。写在文档里的口径是"运行中途中断很常见，
    只有走到了那一步却违约才判 violated"——而一次走到第 5 步、核验和交付还没跑的运行
    是**在飞**，不是违约。这里最容易误报：一份完整可用的成稿被判成
    A5 违约，只因为运行还没结束。

    未归档时这些缺失仍会出现在 ``missing_artifacts`` 里，结论落到 ``incomplete``——
    信息没有丢，只是不再被说成违约。
    """
    return bool(isinstance(meta, dict) and meta.get("closed_at"))


def _real(path: str) -> str:
    """规范化成可比较的真实路径。附件多半在库外，直接比字符串会因为 `~`、
    相对段或软链接漏判。解析失败时退回原字符串，不让审计因为路径问题崩掉。"""
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError):
        return path


# --- A1 入口唯一 -------------------------------------------------------------


def check_entry(run_dir: Path, meta: Any, meta_error: str | None) -> Check:
    check = Check("A1", "入口唯一")
    if meta_error:
        check.fail(meta_error, "meta.json")
        return check
    if not isinstance(meta, dict):
        check.fail("缺 meta.json——没有运行记录说明技能没被真正执行", "meta.json")
        return check
    if meta.get("started_via") not in ENTRY_ALIASES:
        check.fail(
            f"started_via 不是受支持的显式入口 {ENTRY_ALIASES}（{meta.get('started_via')!r}）",
            "meta.json:started_via",
        )
    run_id = str(meta.get("run_id") or "")
    if not run_id:
        check.fail("meta.json 缺 run_id", "meta.json:run_id")
    return check


# --- A2 单品牌隔离 -----------------------------------------------------------


def check_isolation(run_dir: Path, meta: Any) -> Check:
    check = Check("A2", "单品牌隔离")
    kb_root = str((meta or {}).get("kb_root") or "") if isinstance(meta, dict) else ""
    brand = str((meta or {}).get("brand") or "") if isinstance(meta, dict) else ""

    # 老师本次显式提供的附件是合法证据，不受绑定根限制——绑定根约束的是**系统
    # 自主检索的范围**。但只认登记过的：没登记就读，与越界读取在证据上无法区分。
    attachments = {_real(p) for p in run_record.attachment_paths(meta)}
    boundary = str((meta or {}).get("evidence_boundary") or "kb") if isinstance(meta, dict) else "kb"

    def in_run_dir(p: str) -> bool:
        """这条路径指的是本次运行自己的产物吗？

        两种写法都算：绝对路径落在运行目录内（真实运行的形态），或者相对路径在
        运行目录下确实存在（``"evidence.json"`` 这种写法，夹具与手写回执常见）。
        判"是不是自己的产物"要看它指向哪里，不看它怎么写的。
        """
        if workspace.within(p, run_dir):
            return True
        return (not Path(p).is_absolute()) and (run_dir / p).exists()

    def allowed(p: str) -> bool:
        # 运行目录内的文件永远合法。阶段读取上游回执是执行协议规定的正常流程
        # （策略师读 evidence.json、核验员读 program.json、反方读 draft.md），
        # 它不是"检索越过了绑定目录"。A4 已经这样处理了；这里是同一条保证的
        # 第二个位置——第一次只修了 A4，真实运行一跑就把每一次都判成违约。
        if in_run_dir(p):
            return True
        if _real(p) in attachments:
            return True
        return bool(kb_root) and workspace.within(p, kb_root)

    paths = _collect_read_paths(run_dir)
    absolute = [(src, p) for src, p in paths if Path(p).is_absolute()]
    escaping = [(src, p) for src, p in paths if not Path(p).is_absolute() and ".." in Path(p).parts]

    for src, p in escaping:
        check.fail(f"相对路径越出知识库根：{p}", src)

    if absolute and not kb_root and not attachments:
        for src, p in absolute[:5]:
            if in_run_dir(p):
                continue
            check.fail(f"出现绝对路径但 meta.kb_root 为空且未登记附件，无法证明未越界：{p}", src)
    elif absolute:
        for src, p in absolute:
            if not allowed(p):
                check.fail(
                    f"读取了既不在绑定知识库、也不在本次登记附件内的路径：{p}"
                    f"（老师给的文件要先 open --attach 登记）",
                    src,
                )

    # 知识库在本机可达时，进一步核对"声称读过"的文件是否真的存在。
    # 不存在意味着这条引用是凭空写出来的——和杜撰事实是同一类问题。
    if kb_root and Path(kb_root).is_dir():
        for src, p in paths:
            if in_run_dir(p) or (Path(p).is_absolute() and _real(p) in attachments):
                continue
            candidate = Path(p) if Path(p).is_absolute() else Path(kb_root) / p
            if not candidate.exists():
                # 最常见的成因不是杜撰，是路径被写残了：绝对路径掐掉了前缀，于是
                # 既解析不成绝对路径、也不是相对 kb_root 的路径。两种成因的处置不同，
                # 所以要在文案里分开——而"没人能复核这条来源"这个后果是一样的。
                hint = ("路径不完整：既不是绝对路径、也不是相对 kb_root 的路径，没人能复核这条来源"
                        if "/" in p and not Path(p).is_absolute() else "")
                check.fail(f"声称读过但文件不存在：{p}" + (f"（{hint}）" if hint else ""), src)
            elif not allowed(str(candidate)):
                check.fail(f"经软链接指向了知识库之外：{p}", src)

    evidence, err = _load(run_dir, "evidence.json")
    if err:
        check.fail(err, "evidence.json")
    elif isinstance(evidence, dict) and brand:
        if evidence.get("brand") and str(evidence["brand"]) != brand:
            check.fail(
                f"证据回执的品牌（{evidence['brand']}）与本次绑定品牌（{brand}）不一致",
                "evidence.json:brand",
            )

    # 声明了"以附件为准"却在没有申报缺口的情况下展开库内检索，是一次静默的浪费：
    # 老师限定了证据范围，系统仍然把附件驱动的任务做成了知识库普查。判 note 而不是
    # violated——真出现高影响缺口时扩展是对的，缺的只是把缺口写出来。
    #
    # 风格样本不算扩张。"以附件为准"关的是**事实边界**；风格样本不是事实来源，它是
    # "这位客户实际采用过的表达"这一唯一信息，纯附件任务里除了绑定库没有别处能拿。
    # 所以取了风格样本要记进 evidence.style_refs（而不是 facts），这里就不计入。
    if boundary == "attachments" and isinstance(evidence, dict):
        style = {
            _real(s["path"]) for s in (evidence.get("style_refs") or [])
            if isinstance(s, dict) and isinstance(s.get("path"), str)
        } | {
            _real(s) for s in (evidence.get("style_refs") or []) if isinstance(s, str)
        }
        outside = [
            p for p in (evidence.get("read_paths") or [])
            if isinstance(p, str) and _real(p) not in attachments and _real(p) not in style
        ]
        gaps = evidence.get("gaps") or []
        if outside and not gaps:
            check.note(
                f"声明以附件为准，但在没有申报任何缺口的情况下读了 {len(outside)} 个附件之外的文件"
                f"（如确有缺口，写进 evidence.json 的 gaps；如果是风格样本，记进 style_refs）"
            )

    for name in ("verify.json", "verify-a.json", "verify-b.json"):
        data, err = _load(run_dir, name)
        if err:
            check.fail(err, name)
            continue
        if isinstance(data, dict) and data.get("cross_brand"):
            for item in data["cross_brand"]:
                quote = item.get("quote", "") if isinstance(item, dict) else str(item)
                entity = item.get("foreign_entity", "") if isinstance(item, dict) else ""
                check.fail(f"正文混入其他品牌信息：{entity}（{quote[:30]}）", f"{name}:cross_brand")

    if not paths:
        check.note("没有任何 read_paths 记录——无法证明检索发生过")
    return check


# --- A3 动态访谈 -------------------------------------------------------------


def check_interview(run_dir: Path, meta: Any) -> Check:
    check = Check("A3", "动态访谈")
    mode = str((meta or {}).get("mode") or "生成") if isinstance(meta, dict) else "生成"
    data, err = _load(run_dir, "interview.json")
    if err:
        check.fail(err, "interview.json")
        return check
    if data is None:
        if mode != "生成":
            check.skip(f"{mode} 模式不要求访谈记录")
        elif _drafts(run_dir):
            check.fail("已经成稿却没有访谈记录——写作发生在理解任务之前", "interview.json")
        else:
            check.skip("运行还没走到访谈阶段（缺 interview.json）")
        return check
    if not isinstance(data, dict):
        check.fail("interview.json 顶层不是对象", "interview.json")
        return check

    rounds = data.get("rounds")
    if not isinstance(rounds, list):
        check.fail("interview.json 没有 rounds", "interview.json:rounds")
        return check

    stopped = str(data.get("stopped_because") or "").strip()

    # 零轮访谈是合法结果，不是违约。方法论第四条原则是"只解决会改变当前稿件的
    # 不确定性"——老师把交付合同和封闭证据边界都给全了的时候，一个合法问题都没有
    # 才是对的。判它违约会训练出"为了过审计随便问一句"的行为，而那比不问更糟。
    # 但必须写清为什么没问：没有理由的零轮和"根本没访谈"在证据上不可区分。
    if not rounds:
        if not stopped:
            check.fail(
                "零轮访谈且没写 stopped_because——无法区分"
                "「问题都已被附件回答」与「根本没做访谈」",
                "interview.json:stopped_because",
            )
        else:
            # 零轮同样要说清评估的是哪一维。"没什么要问的"和"这两个高影响项已被
            # 附件闭合"在证据上是两回事，而只有后者能被复核。
            _check_stop_reason(check, stopped, zero_round=True)
        _check_fake_confidence(check, data)
        return check

    for i, item in enumerate(rounds, 1):
        question = str((item or {}).get("question") or "") if isinstance(item, dict) else ""
        if not question.strip():
            check.fail(f"第 {i} 轮没有记录问题原文", f"interview.json:rounds[{i - 1}].question")
            continue
        if _question_count(question) > 1:
            check.fail(
                f"第 {i} 轮一次问了多个问题（违反每轮一问）：{question[:40]}",
                f"interview.json:rounds[{i - 1}].question",
            )

    empty_streak = 0
    for i, item in enumerate(rounds, 1):
        changed = str((item or {}).get("changed") or "").strip() if isinstance(item, dict) else ""
        empty_streak = empty_streak + 1 if not changed else 0
        if empty_streak >= 2:
            check.fail(
                f"第 {i - 1}、{i} 轮都没有改变任何判断——在问无效问题",
                f"interview.json:rounds[{i - 1}].changed",
            )
            break

    # 同一个问题问两遍：老师的第二次回答会开始敷衍，而敷衍的回答比没有回答更危险。
    # 判据是归一化后重复，不做语义相似度——语义判断会误伤"同一话题的不同追问"。
    seen: dict[str, int] = {}
    for i, item in enumerate(rounds, 1):
        question = str((item or {}).get("question") or "") if isinstance(item, dict) else ""
        key = _normalize_question(question)
        if not key:
            continue
        if key in seen:
            check.fail(
                f"第 {seen[key]} 轮与第 {i} 轮问的是同一个问题：{question[:40]}"
                f"（问前要先读已有回答、附件与索引）",
                f"interview.json:rounds[{i - 1}].question",
            )
        else:
            seen[key] = i

    if not stopped:
        check.fail("缺 stopped_because——无法判断访谈是按动态充分性停的还是随手停的",
                   "interview.json:stopped_because")
    else:
        _check_stop_reason(check, stopped, zero_round=False)
    _check_fake_confidence(check, data)
    return check



# --- A4 阶段边界 -------------------------------------------------------------


def check_stages(run_dir: Path, meta: Any) -> Check:
    check = Check("A4", "阶段边界")

    for name, expected in STAGE_ID_BY_FILE.items():
        data, err = _load(run_dir, name)
        if err:
            check.fail(err, name)
            continue
        if not isinstance(data, dict):
            continue
        actual = data.get("role")
        if actual and actual != expected:
            check.fail(f"{name} 的 role 是 {actual!r}，应为 {expected!r}", f"{name}:role")

    # 写作者不得重新扫描知识库：读过的路径必须在写作程序授权范围内
    program, err = _load(run_dir, "program.json")
    if err:
        check.fail(err, "program.json")
        program = None
    authorized: set[str] = set()
    if isinstance(program, dict):
        for key in ("authorized_reads", "style_refs"):
            for value in program.get(key) or []:
                if isinstance(value, str):
                    # 原样和归一化两种形式都收。附件是绝对路径，而 /tmp 与
                    # /private/tmp 这类软链接会让"同一个文件"在两侧写成两个字符串——
                    # 只比原样会把写作者读一份已授权的附件误判成越权。
                    authorized.add(value)
                    authorized.add(_real(value))

    for name in ("write-receipt.json", "write-receipt-a.json", "write-receipt-b.json"):
        data, err = _load(run_dir, name)
        if err:
            check.fail(err, name)
            continue
        if not isinstance(data, dict):
            continue
        reads = [p for p in (data.get("read_paths") or []) if isinstance(p, str)]
        reads += [p for p in (data.get("style_refs_used") or []) if isinstance(p, str)]
        for p in reads:
            # 运行目录内的文件永远算授权。写作者的输入就是 program.json，它读自己的
            # 任务单不是"重新扫描知识库"——而 A4 守的恰恰只有后者。不放过这一条会
            # 制造一个纯粹的误报：主智能体忘了把 program.json 写进 authorized_reads，
            # 写作者读了它，审计就判越权——靠主智能体自觉补上不是契约。
            if workspace.within(p, run_dir):
                continue
            if p not in authorized and _real(p) not in authorized:
                check.fail(
                    f"写作者读了写作程序未授权的文件：{p}（写作阶段重新选素材会让取舍问题与表达问题无法区分）",
                    f"{name}:read_paths",
                )
        if "deviations" not in data:
            check.fail("写作回执缺 deviations 字段——未申报的偏离是最难定位的一类问题",
                       f"{name}:deviations")

    # 有正文却没有写作回执，等于无法归因
    receipts = [n for n in ("write-receipt.json", "write-receipt-a.json", "write-receipt-b.json")
                if (run_dir / n).is_file()]
    if _drafts(run_dir) and not receipts:
        if _archived(meta):
            check.fail("运行已归档，有正文但没有写作回执，表达问题无法归因", "draft.md")
        else:
            check.skip("有正文但还没有写作回执（运行未归档，可能仍在成稿阶段）")

    # 核验与反方不得改稿
    for name in ("verify.json", "verify-a.json", "verify-b.json", "adversary.json"):
        data, _ = _load(run_dir, name)
        if not isinstance(data, (dict, list)):
            continue
        for key_path, value in _walk_strings(data, REWRITE_KEYS):
            if value not in (None, "", [], {}):
                check.fail(f"{name} 出现改写后的内容（{key_path}）——审查阶段只返回问题，不改稿", name)
                break
    return check


# --- A5 输出有效 -------------------------------------------------------------


def _expected_verdict(data: dict[str, Any]) -> str:
    claims = data.get("claims") or []
    judgements = [str((c or {}).get("judgement") or "") for c in claims if isinstance(c, dict)]
    coverage = data.get("coverage") or {}
    unsourced = int(coverage.get("unsourced") or judgements.count("unsourced"))
    drifted = int(coverage.get("drifted") or judgements.count("drifted"))
    stale = int(coverage.get("stale") or judgements.count("stale"))
    if unsourced or data.get("cross_brand") or data.get("redline_hits"):
        return "暂不建议提交"
    if drifted or stale:
        return "有待确认项"
    return "可进入人工初审"


def check_output(run_dir: Path, meta: Any) -> Check:
    check = Check("A5", "输出有效")
    mode = str((meta or {}).get("mode") or "生成") if isinstance(meta, dict) else "生成"
    if mode != "生成":
        check.skip(f"{mode} 模式不产出正文")
        return check

    drafts = _drafts(run_dir)
    if not drafts:
        check.skip("运行还没成稿（缺 draft.md）")
        return check

    verify_names = [n for n in ("verify.json", "verify-a.json", "verify-b.json")
                    if (run_dir / n).is_file()]
    if not verify_names:
        if _archived(meta):
            check.fail("运行已归档却没有来源核验回执——「事实有来源」这个结论不成立", "verify.json")
        else:
            check.skip("还没有来源核验回执（运行未归档，可能仍在核验阶段）")
    # 只算真的被打开过的文件。检索候选不算——见 _collect_read_paths 的说明。
    opened = {p for _, p in _collect_read_paths(run_dir, include_retrievals=False)}
    declared = opened | {_real(p) for p in opened}

    for name in verify_names:
        data, err = _load(run_dir, name)
        if err:
            check.fail(err, name)
            continue
        if not isinstance(data, dict):
            check.fail(f"{name} 顶层不是对象", name)
            continue

        coverage = data.get("coverage") or {}
        claims = [c for c in (data.get("claims") or []) if isinstance(c, dict)]
        unsourced = int(coverage.get("unsourced") or 0) or sum(
            1 for c in claims if c.get("judgement") == "unsourced"
        )
        if unsourced:
            check.fail(f"{unsourced} 处事实性表达找不到来源（等价于杜撰）", f"{name}:coverage.unsourced")
        if data.get("redline_hits"):
            check.fail("命中客户红线", f"{name}:redline_hits")

        expected = _expected_verdict(data)
        actual = str(data.get("verdict") or "")
        if actual not in VERDICTS:
            check.fail(f"verdict 必须是 {VERDICTS} 之一，收到 {actual!r}", f"{name}:verdict")
        elif actual != expected:
            check.fail(f"结论口径不符：按核验结果应为「{expected}」，写的是「{actual}」", f"{name}:verdict")

        sources = [s for s in (data.get("sources_used") or []) if isinstance(s, dict)]
        if not sources:
            check.fail("sources_used 为空——交付物必须给出实际来源清单", f"{name}:sources_used")
        for item in sources:
            ref = str(item.get("path_or_url") or "")
            if not ref:
                check.fail("sources_used 有条目缺 path_or_url", f"{name}:sources_used")
                continue
            if ref.startswith(("http://", "https://")):
                continue
            if declared and ref not in declared and _real(ref) not in declared:
                check.fail(
                    f"来源清单里出现没有被任何阶段打开过的文件：{ref}"
                    f"（检索候选不算读过——retrieve 不返回全文）",
                    f"{name}:sources_used",
                )

    delivery = _text(run_dir, "delivery.md")
    if delivery is None:
        # 只有归档过的运行才判违约。一次走到第 7 步、还没交付的运行是在飞，不是违约——
        # 而"运行中途中断很常见，只有走到了那一步却违约才判 violated"是写在文档里的口径。
        # 判据是 meta.closed_at：它是"这次运行结束了"的唯一显式信号。
        if _archived(meta):
            check.fail("运行已归档却没有 delivery.md——业务侧实际收到什么无从查证", "delivery.md")
        else:
            check.skip("运行还没归档，尚未走到交付（缺 delivery.md）")
    else:
        if not any(v in delivery for v in VERDICTS):
            check.fail(f"交付内容里没有结论行（{'/'.join(VERDICTS)}）", "delivery.md")
        if "实际来源" not in delivery:
            check.fail("交付内容里没有实际来源清单", "delivery.md")
        for leak in ("evidence.json", "strategy.json", "program.json", ".blueink/runs"):
            if leak in delivery:
                check.fail(f"交付内容泄漏了技术轨迹（{leak}）——业务侧不该看到这些", "delivery.md")
                break

    # 取证报了冲突，但写作程序既没有 assumptions 也没有决策卡：这一组冲突要么由老师
    # 在访谈里裁决过，要么被静默处理了——而"静默选边"是方法论里最重的一条失职，
    # 因为它把一个可见的分歧变成一个不可见的错误。
    #
    # 判 note 而不是 violated：老师在访谈里逐条裁决过时，assumptions 合法地为空，
    # 而"他到底裁决了没有"机器判不准。误报会让人学会忽略整条 A5，代价比这条漏网更大。
    evidence, _ = _load(run_dir, "evidence.json")
    program, _ = _load(run_dir, "program.json")
    if isinstance(evidence, dict) and isinstance(program, dict):
        conflicts = [c for c in (evidence.get("conflicts") or []) if c]
        assumptions = [a for a in (program.get("assumptions") or []) if a]
        if conflicts and not assumptions and not (run_dir / "decision-card.md").is_file():
            check.note(
                f"证据里有 {len(conflicts)} 组冲突，而写作程序既没有 assumptions "
                f"也没有决策卡——冲突要么由老师在访谈里逐条裁决过，要么被静默处理了"
            )
    return check


# --- 汇总 -------------------------------------------------------------------


SCHEMA_4_REQUIRED_BY_MODE = {
    "生成": [("run.json",), ("draft.md",), ("verify.json",), ("delivery.md",)],
    "绑定": [("run.json",)],
    "学习": [("run.json",)],
    "定位": [("run.json",)],
}

CURRENT_REQUIRED_BY_MODE = {
    "生成": [("run.json",), ("delivery.md",), ("verify.json",), ("delivery-check.md",)],
    "绑定": [("run.json",)],
    "学习": [("run.json",)],
    "定位": [("run.json",)],
}

LEGACY_GENERATION_FILES = {
    "meta.json", "interview.json", "evidence.json", "strategy.json", "program.json",
    "write-receipt.json", "adversary.json",
}


def _fast_source_allowed(ref: str, record: dict[str, Any]) -> bool:
    if ref.startswith(("http://", "https://")):
        return True
    kb_root = str(record.get("kb_root") or "")
    raw = Path(ref).expanduser()
    resolved = _real(str((Path(kb_root) / raw) if kb_root and not raw.is_absolute() else raw))
    attachments = {_real(path) for path in run_record.attachment_paths(record)}
    if resolved in attachments:
        return True
    return bool(kb_root) and workspace.within(resolved, kb_root)


def _fast_check_entry(record: Any, error: str | None) -> Check:
    check = Check("A1", "入口唯一")
    if error:
        check.fail(error, "run.json")
        return check
    if not isinstance(record, dict):
        check.fail("缺 run.json——没有新版运行记录", "run.json")
        return check
    if record.get("started_via") not in ENTRY_ALIASES:
        check.fail(f"started_via 不是受支持的显式入口 {ENTRY_ALIASES}", "run.json:started_via")
    if not str(record.get("run_id") or ""):
        check.fail("run.json 缺 run_id", "run.json:run_id")
    if record.get("schema_version") not in (4, run_record.CURRENT_SCHEMA_VERSION):
        check.fail(
            f"run.json.schema_version 必须为 4 或 {run_record.CURRENT_SCHEMA_VERSION}",
            "run.json:schema_version",
        )
    return check


def _fast_check_isolation(record: Any, verify: Any) -> Check:
    check = Check("A2", "单品牌隔离")
    if not isinstance(record, dict):
        check.fail("run.json 顶层不是对象", "run.json")
        return check
    for index, fact in enumerate(record.get("facts") or []):
        if not isinstance(fact, dict):
            check.fail(f"facts[{index}] 不是对象", f"run.json:facts[{index}]")
            continue
        source = str(fact.get("source_path") or "")
        if not source or not _fast_source_allowed(source, record):
            check.fail(f"事实来源未登记或越过绑定知识库：{source}", f"run.json:facts[{index}]")
    if isinstance(verify, dict):
        for index, item in enumerate(verify.get("sources_used") or []):
            ref = str((item or {}).get("path_or_url") or "") if isinstance(item, dict) else ""
            if not ref or not _fast_source_allowed(ref, record):
                check.fail(f"交付来源未登记或越界：{ref}", f"verify.json:sources_used[{index}]")
        if verify.get("cross_brand"):
            check.fail("正文存在跨品牌信息", "verify.json:cross_brand")
    return check


def _fast_check_interview(record: Any) -> Check:
    check = Check("A3", "动态访谈")
    if not isinstance(record, dict):
        check.fail("run.json 顶层不是对象", "run.json")
        return check
    if str(record.get("mode") or "生成") != "生成":
        check.skip(f"{record.get('mode')} 模式不要求成稿前方向确认")
        return check
    interview = record.get("interview")
    direction = record.get("direction")
    rounds = interview.get("rounds") if isinstance(interview, dict) else None
    if not isinstance(rounds, list) or not rounds:
        check.fail("生成任务不允许零轮访谈", "run.json:interview.rounds")
        return check
    ordinary = 0
    direction_round = False
    for index, item in enumerate(rounds, 1):
        if not isinstance(item, dict):
            check.fail(f"第 {index} 轮不是对象", f"run.json:interview.rounds[{index - 1}]")
            continue
        kind = str(item.get("kind") or "")
        ordinary += kind != "hard_conflict"
        direction_round = direction_round or kind == "direction"
        question = str(item.get("question") or "")
        answer = str(item.get("answer") or "")
        if not question.strip() or not answer.strip():
            check.fail(f"第 {index} 轮缺问题或老师原话", f"run.json:interview.rounds[{index - 1}]")
        if _question_count(question) > 1:
            check.fail(f"第 {index} 轮一次问了多个问题", f"run.json:interview.rounds[{index - 1}].question")
    if ordinary > 2:
        check.fail("普通生成超过两轮；额外轮次只能是事实或来源 hard_conflict", "run.json:interview.rounds")
    if rounds and isinstance(rounds[-1], dict) and rounds[-1].get("kind") != "direction":
        check.fail("生成任务最后一轮不是成稿前方向确认", "run.json:interview.rounds")
    if not direction_round:
        check.fail("缺成稿前方向确认轮", "run.json:interview.rounds")
    if not isinstance(direction, dict) or direction.get("confirmed_by_user") is not True:
        check.fail("方向没有得到老师明确确认", "run.json:direction.confirmed_by_user")
    return check


def _fast_check_stages(run_dir: Path, record: Any, verify: Any, verify_error: str | None) -> Check:
    check = Check("A4", "阶段边界")
    if verify_error:
        check.fail(verify_error, "verify.json")
    present = {path.name for path in run_dir.iterdir() if path.is_file()}
    obsolete = set(LEGACY_GENERATION_FILES)
    if isinstance(record, dict) and record.get("schema_version") == run_record.CURRENT_SCHEMA_VERSION:
        obsolete.update({"draft.md", "draft-a.md", "draft-b.md"})
    leaked = sorted(present & obsolete)
    if leaked:
        check.fail(f"新版运行仍生成旧阶段文件：{'、'.join(leaked)}", leaked[0])
    if isinstance(record, dict) and str(record.get("mode") or "") == "生成":
        if record.get("decision") is None or record.get("direction") is None:
            check.fail("成稿前没有保存方向与编辑决策", "run.json:decision")
        facts = record.get("facts") or []
        if len(facts) > 12:
            check.fail("事实原子超过 12 条", "run.json:facts")
        for index, fact in enumerate(facts):
            if not isinstance(fact, dict):
                continue
            allowed = fact.get("allowed_strong_words") or []
            if any(word not in run_record.STRONG_WORDS for word in allowed):
                check.fail(f"facts[{index}] 含非法强比较词授权", f"run.json:facts[{index}]")
    if isinstance(verify, dict) and verify.get("role") != "single-agent-verifier":
        check.fail("verify.json 没有声明同一智能体受限复核", "verify.json:role")
    return check


def _fast_check_output(run_dir: Path, record: Any, verify: Any) -> Check:
    check = Check("A5", "输出有效")
    if not isinstance(record, dict) or str(record.get("mode") or "生成") != "生成":
        check.skip("当前模式不产出正文")
        return check
    current = record.get("schema_version") == run_record.CURRENT_SCHEMA_VERSION
    body_name = "delivery.md" if current else "draft.md"
    body = _text(run_dir, body_name)
    if body is None:
        check.skip(f"运行还没成稿（缺 {body_name}）")
        return check
    decision = record.get("decision")
    fast_path = isinstance(decision, dict) and decision.get("path") in {
        "attachment-draft-first", "attachment-delivery-first",
    }
    if not fast_path:
        allowed_words = {
            word for fact in (record.get("facts") or []) if isinstance(fact, dict)
            for word in (fact.get("allowed_strong_words") or [])
        }
        for word in run_record.STRONG_WORDS:
            if word in body and word not in allowed_words:
                check.fail(f"正文使用未获来源范围授权的强比较词「{word}」", body_name)

    if not isinstance(verify, dict):
        if _archived(record):
            check.fail("运行已归档却没有合法 verify.json", "verify.json")
        else:
            check.skip("尚未保存核验结论")
        return check
    expected = _expected_verdict(verify)
    if verify.get("verdict") != expected:
        check.fail(f"核验结论应为「{expected}」", "verify.json:verdict")
    if not (verify.get("sources_used") or []):
        check.fail("sources_used 为空", "verify.json:sources_used")
    if fast_path:
        reviewed = [
            str(item.get("quote") or "") for item in (verify.get("risk_sentences") or [])
            if isinstance(item, dict)
        ]
        for word in run_record.STRONG_WORDS:
            if word in body and not any(word in quote for quote in reviewed):
                check.fail(f"附件稿件里的强比较词「{word}」没有进入轻量复核清单", "verify.json")

    if current:
        for mixed_section in ("交付核对卡", "交付前核对卡", "实际来源"):
            if mixed_section in body:
                check.fail(f"delivery.md 混入{mixed_section}，正文与核对信息没有分开", "delivery.md")
        delivery_check = _text(run_dir, "delivery-check.md")
        if delivery_check is None:
            if _archived(record):
                check.fail("运行已归档却没有 delivery-check.md", "delivery-check.md")
            else:
                check.skip("运行尚未归档，delivery-check.md 还未生成")
        else:
            if str(verify.get("verdict") or "") not in delivery_check:
                check.fail("delivery-check.md 没有使用 verify.json 的结论", "delivery-check.md")
            if "实际来源" not in delivery_check:
                check.fail("delivery-check.md 缺实际来源", "delivery-check.md")
    else:
        delivery = _text(run_dir, "delivery.md")
        if delivery is None:
            if _archived(record):
                check.fail("运行已归档却没有 delivery.md", "delivery.md")
            else:
                check.skip("运行尚未归档，delivery.md 还未生成")
        else:
            if str(verify.get("verdict") or "") not in delivery:
                check.fail("delivery.md 没有使用 verify.json 的结论", "delivery.md")
            if "实际来源" not in delivery:
                check.fail("delivery.md 缺实际来源", "delivery.md")
    artifact_name = "delivery-check.md" if current else "delivery.md"
    artifact = _text(run_dir, artifact_name)
    if artifact is not None:
        for leak in ("run.json", "verify.json", ".blueink/runs"):
            if leak in artifact:
                check.fail(f"交付泄漏技术轨迹：{leak}", artifact_name)
                break
    return check


def audit_fast(run_dir: Path) -> dict[str, Any]:
    record, record_error = _load(run_dir, "run.json")
    verify, verify_error = _load(run_dir, "verify.json")
    checks = [
        _fast_check_entry(record, record_error),
        _fast_check_isolation(record, verify),
        _fast_check_interview(record),
        _fast_check_stages(run_dir, record, verify, verify_error),
        _fast_check_output(run_dir, record, verify),
    ]
    mode = str((record or {}).get("mode") or "生成") if isinstance(record, dict) else "生成"
    present = {path.name for path in run_dir.iterdir() if path.is_file()}
    required = (
        CURRENT_REQUIRED_BY_MODE
        if isinstance(record, dict) and record.get("schema_version") == run_record.CURRENT_SCHEMA_VERSION
        else SCHEMA_4_REQUIRED_BY_MODE
    )
    missing = [
        group[0] for group in required.get(mode, [])
        if not any(name in present for name in group)
    ]
    failed = [check.id for check in checks if check.status == "fail"]
    verdict = "violated" if failed else ("incomplete" if missing else "pass")
    return {
        "run_id": (record or {}).get("run_id") if isinstance(record, dict) else None,
        "brand": (record or {}).get("brand") if isinstance(record, dict) else None,
        "mode": mode,
        "verdict": verdict,
        "checks": [check.as_dict() for check in checks],
        "failed": failed,
        "missing_artifacts": missing,
    }


def audit(run_dir: str | Path) -> dict[str, Any]:
    """审计一次运行，返回结论字典。"""
    path = Path(run_dir)
    if not path.is_dir():
        return {
            "run_id": None,
            "verdict": "violated",
            "checks": [],
            "failed": ["A1"],
            "missing_artifacts": [],
            "error": f"运行记录目录不存在：{path}",
        }

    if (path / "run.json").is_file():
        return audit_fast(path)

    meta, meta_error = _load(path, "meta.json")
    mode = str((meta or {}).get("mode") or "生成") if isinstance(meta, dict) else "生成"

    checks = [
        check_entry(path, meta, meta_error),
        check_isolation(path, meta),
        check_interview(path, meta),
        check_stages(path, meta),
        check_output(path, meta),
    ]

    present = {p.name for p in path.iterdir() if p.is_file()}
    missing = [
        group[0] for group in REQUIRED_BY_MODE.get(mode, [])
        if not any(name in present for name in group)
    ]

    failed = [c.id for c in checks if c.status == "fail"]
    if failed:
        verdict = "violated"
    elif missing:
        verdict = "incomplete"
    else:
        verdict = "pass"

    return {
        "run_id": (meta or {}).get("run_id") if isinstance(meta, dict) else None,
        "brand": (meta or {}).get("brand") if isinstance(meta, dict) else None,
        "mode": mode,
        "verdict": verdict,
        "checks": [c.as_dict() for c in checks],
        "failed": failed,
        "missing_artifacts": missing,
    }


# --- 结论自检：审计结论本身是否可用 -----------------------------------------
#
# 这五项检查的对象是 audit 的输出，而不是运行记录。它们保证一件事：**审计结论
# 永远能把问题定位到具体文件**。评测就是拿这五项跑 evals/golden 下的夹具。

CONTRACT_NAMES = ["入口唯一", "单品牌隔离", "动态访谈", "阶段边界", "输出有效"]
CONTRACT_IDS = ["A1", "A2", "A3", "A4", "A5"]
VALID_STATUS = {"pass", "fail", "skip"}
VERDICT_CHECKS = ("schema", "consistent", "localisable", "explained", "contracts")


def verify_verdict(data: Any, which: str) -> list[str]:
    """检查一份审计结论。返回问题列表，空列表表示通过。"""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["结论顶层不是对象"]

    checks = data.get("checks")
    if which == "schema":
        for field in ("run_id", "mode", "verdict", "checks", "failed", "missing_artifacts"):
            if field not in data:
                problems.append(f"缺字段 {field}")
        if data.get("verdict") not in ("pass", "violated", "incomplete"):
            problems.append(f"verdict 取值非法：{data.get('verdict')!r}")
        if not isinstance(checks, list) or len(checks) != len(CONTRACT_IDS):
            problems.append(f"checks 应恰好 {len(CONTRACT_IDS)} 条，实际 {len(checks or [])} 条")
        else:
            if [c.get("id") for c in checks] != CONTRACT_IDS:
                problems.append(f"checks 的 id 应为 {CONTRACT_IDS}")
            for i, check in enumerate(checks):
                for field in ("id", "name", "status", "detail", "evidence"):
                    if field not in check:
                        problems.append(f"checks[{i}] 缺字段 {field}")
                if check.get("status") not in VALID_STATUS:
                    problems.append(f"checks[{i}] status 非法：{check.get('status')!r}")
        return problems

    if not isinstance(checks, list):
        return ["checks 不是列表"]

    if which == "consistent":
        failed = data.get("failed")
        missing = data.get("missing_artifacts")
        if not isinstance(failed, list) or not isinstance(missing, list):
            return ["failed 与 missing_artifacts 都必须是列表"]
        actual_failed = sorted(c.get("id") for c in checks if c.get("status") == "fail")
        if sorted(failed) != actual_failed:
            problems.append(f"failed={sorted(failed)} 与 checks 里的 fail 项 {actual_failed} 不一致")
        expected = "violated" if failed else ("incomplete" if missing else "pass")
        if data.get("verdict") != expected:
            problems.append(f"verdict 应为 {expected}，实际 {data.get('verdict')!r}")
        return problems

    if which == "localisable":
        for check in checks:
            if check.get("status") != "fail":
                continue
            evidence = check.get("evidence") or []
            if not evidence:
                problems.append(f"{check.get('id')} 违约但没给 evidence，无法定位到文件")
                continue
            for ref in evidence:
                if not isinstance(ref, str) or (".json" not in ref and ".md" not in ref):
                    problems.append(f"{check.get('id')} 的 evidence 未指向具体文件：{ref!r}")
        return problems

    if which == "explained":
        for check in checks:
            detail = str(check.get("detail") or "").strip()
            if not detail:
                problems.append(f"{check.get('id')} 没有说明")
            elif check.get("status") == "skip" and detail == "通过":
                problems.append(f"{check.get('id')} 被跳过却写成「通过」")
        return problems

    if which == "contracts":
        names = [c.get("name") for c in checks]
        if names != CONTRACT_NAMES:
            problems.append(f"五项契约名称应为 {CONTRACT_NAMES}，实际 {names}")
        return problems

    return [f"未知的自检项：{which}"]


def verify_verdict_file(path: str | Path, which: str) -> list[str]:
    """从文件读一份审计结论并自检。"""
    target = Path(path)
    if not target.is_file():
        return [f"找不到结论文件：{target}"]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"结论文件无法解析：{exc}"]
    return verify_verdict(data, which)
