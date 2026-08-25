#!/usr/bin/env python3
"""状态层回归测试：确定性那一半必须先没有静默失败。

这个技能把"出问题能定位"押在一批确定性组件上：绑定边界、旁路索引、检索分轨、
URL 白名单、条件化记忆、审计器。它们如果自己会静默出错，上面那句承诺就是空的。

这里测的都是**真实发生过或攻击得到的失败形态**，不是覆盖率：

- 绑到品牌集合层能不能被拦住（拦不住 → 一稿混用两套品牌表达）
- 换品牌／换知识库改绑能不能被正确处理（否则索引归属会静默失真）
- 历史技能包的 references 会不会被当经验证据取出来（会 → 外部固定模板接管本次判断）
- 只有元数据的文件会不会被当成"已检索"
- 符号链接会不会把检索带出绑定目录，跳过项有没有被报出来
- URL 白名单能不能挡住后缀／用户名／路径／IP 四类伪装
- 独立事件能不能凭空编出来（能 → 偶发偏好被推到高置信度并自动进写作程序）
- 反例是降置信度还是删旧结论
- 审计器能不能定位到具体文件

用法：``python3 scripts/test_state.py``；退出码 0 全过。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit as audit_mod        # noqa: E402
import blueink as blueink_cli    # noqa: E402
import index_kb                  # noqa: E402
import memory as memory_mod      # noqa: E402
import miniyaml                  # noqa: E402
import official as official_mod  # noqa: E402
import retrieve as retrieve_mod  # noqa: E402
import run_record                # noqa: E402
import workspace                 # noqa: E402

FAILURES: list[str] = []
CHECKED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global CHECKED
    CHECKED += 1
    if condition:
        return
    FAILURES.append(f"{name}" + (f"：{detail}" if detail else ""))


def expect_raises(name: str, exc: type[BaseException], fn, *args, **kwargs) -> BaseException | None:
    try:
        fn(*args, **kwargs)
    except exc as caught:
        return caught
    except Exception as other:  # noqa: BLE001
        check(name, False, f"抛的是 {type(other).__name__}：{other}")
        return None
    check(name, False, "没有抛异常")
    return None


# --- 夹具 -------------------------------------------------------------------


def make_docx(path: Path, text: str) -> None:
    """造一个最小可解析的 docx，用来验证 Office 正文抽取。

    刻意不带 OOXML 命名空间：抽取走的是 ``itertext()``，它不看标签名，所以命名空间
    对这条代码路径没有影响；而写进夹具会让人误以为技能依赖那个域名。真实 docx（带完整
    命名空间、来自实际品牌知识库）已单独验证过，不靠这个夹具证明。
    """
    body = (
        '<?xml version="1.0"?>'
        f"<document><body><p><r><t>{text}</t></r></p></body></document>"
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", body)


def build_kb(root: Path) -> None:
    """造一个像真实源库的知识库：分轨语料 + 一个历史技能包 + 二进制文件。"""
    (root / "02-原文资产").mkdir(parents=True)
    (root / "03-初终稿对比").mkdir(parents=True)
    (root / "05-成品参考").mkdir(parents=True)
    (root / "04-经验总结原稿" / "brandx-pr-writer" / "references").mkdir(parents=True)

    (root / "02-原文资产" / "需求素材.md").write_text(
        "# 需求素材\n本次上市交付数据与产能规划。交付 31767 辆。\n", encoding="utf-8"
    )
    (root / "03-初终稿对比" / "【初稿】上市稿.md").write_text(
        "# 初稿\n在行业变革的大背景下，本次上市……\n", encoding="utf-8"
    )
    (root / "03-初终稿对比" / "【终稿】上市稿.md").write_text(
        "# 终稿\n上市首月交付 31767 辆，验证了平台的量产能力。\n", encoding="utf-8"
    )
    (root / "05-成品参考" / "新闻稿参考.md").write_text(
        "# 新闻稿参考\n交付数据与平台技术支撑。\n", encoding="utf-8"
    )
    # 老师手写的经验总结：必须仍然可检索
    (root / "04-经验总结原稿" / "客户对接经验.md").write_text(
        "# 对接经验\n传播红线：不拉踩竞品。新闻稿结构不强制铺垫行业背景。\n", encoding="utf-8"
    )
    # 混进来的写作技能包：整棵子树都该被隔离
    skill_dir = root / "04-经验总结原稿" / "brandx-pr-writer"
    (skill_dir / "SKILL.md").write_text("# 某写作技能\n新闻稿必须先铺垫行业背景。\n", encoding="utf-8")
    (skill_dir / "references" / "press_release_templates.md").write_text(
        "# 新闻稿模板\n第一段：行业背景。第二段：产品结构。\n", encoding="utf-8"
    )
    # 二进制：pdf 只有元数据，docx 能取正文
    (root / "02-原文资产" / "参考资料.pdf").write_bytes(b"%PDF-1.4 not really a pdf")
    make_docx(root / "02-原文资产" / "品牌红线.docx", "品牌红线：禁止臆造非官方 logo。")


# --- 测试 -------------------------------------------------------------------


def kb_of(project: Path) -> Path:
    """当前工作空间绑定的知识库根。测试要改源库文件时从这里取路径。"""
    return Path(str(workspace.load(project)["kb_root"]))


def test_cross_platform_config() -> None:
    """Windows 盘符与 UNC 路径写进 workspace.yaml 后必须原样读回。"""
    for value in (
        r"C:\Users\张老师\Documents\理想汽车知识库",
        r"C:\Users\Zhang San\Documents\Brand KB",
        r"\\fileserver\brand-share\理想汽车",
    ):
        encoded = miniyaml.dumps({"kb_root": value})
        decoded = miniyaml.loads(encoded)
        check(f"Windows 路径往返不变：{value}", decoded["kb_root"] == value,
              f"{encoded!r} -> {decoded!r}")


def test_bind(tmp: Path, kb: Path) -> Path:
    # 家目录不能成为所有下级项目的隐式工作空间。用可替换常量造一个假家目录，
    # 不读取或修改运行测试这台机器的真实 HOME。
    fake_home = tmp / "home"
    child = fake_home / "projects" / "brand-a"
    (fake_home / ".blueink").mkdir(parents=True)
    child.mkdir(parents=True)
    original_home = workspace.HOME_DIR
    workspace.HOME_DIR = fake_home.resolve()
    try:
        check("下级项目不继承家目录工作空间", workspace.find_project_root(child) is None)
        expect_raises("禁止把家目录绑定成项目根", workspace.WorkspaceError,
                      workspace.bind, "品牌甲", kb, start=fake_home)
    finally:
        workspace.HOME_DIR = original_home

    project = tmp / "proj"
    project.mkdir()

    # 品牌集合层必须拦住，而不只是告警
    collection = tmp / "brands"
    for name in ("品牌甲", "品牌乙"):
        (collection / name).mkdir(parents=True)
        (collection / name / "a.md").write_text("x", encoding="utf-8")
    expect_raises("绑到品牌集合层应被拦截", workspace.WorkspaceError,
                  workspace.bind, "品牌甲", collection, start=tmp / "p_col")
    (tmp / "p_col").mkdir(exist_ok=True)
    data, _ = workspace.bind("品牌甲", collection, force=True, start=tmp / "p_col")
    check("--force 可以绕过集合层拦截", data["brand"] == "品牌甲")

    # 不存在的目录
    expect_raises("绑不存在的目录应报错", workspace.WorkspaceError,
                  workspace.bind, "品牌甲", tmp / "查无此目录", start=project)

    data, warnings = workspace.bind(
        "品牌甲", kb, official_urls=["https://www.example.com/news"], start=project
    )
    check("绑定写入品牌", data["brand"] == "品牌甲")
    check("工作空间只返回当前字段", set(data) == set(workspace.WORKSPACE_FIELDS), str(sorted(data)))
    check("官方来源归一化到主域", data["official_sources"][0]["url"] == "https://example.com",
          str(data["official_sources"]))
    check("路径被丢弃时有告警", any("/news" in w for w in warnings), str(warnings))
    ignore = project / ".blueink" / ".gitignore"
    check("本地工作空间默认不进版本控制", ignore.read_text(encoding="utf-8") == "*\n!.gitignore\n")

    # 品牌变化必须使用独立项目，--force 不绕过品牌隔离。
    exc = expect_raises("改品牌应被拦截", workspace.WorkspaceError, workspace.bind,
                        "品牌乙", kb, start=project)
    if exc is not None:
        check("改品牌的报错要求新项目", "新建项目" in str(exc), str(exc))
    expect_raises("改品牌加 --force 也应被拦截", workspace.WorkspaceError,
                  workspace.bind, "品牌乙", kb, force=True, start=project)

    # 当前品牌换电脑或移动知识库：只迁移路径，清空可重建索引，
    # 保留官方白名单、学习目录与历史运行。
    moved = tmp / "kb_moved"
    shutil.copytree(kb, moved)
    stale = project / ".blueink" / "index" / "index.json"
    stale.write_text('{"stale": true}', encoding="utf-8")
    expect_raises("知识库路径变化未确认时拒绝", workspace.WorkspaceError,
                  workspace.bind, "品牌甲", moved, start=project)
    migrated, migrated_warnings = workspace.bind(
        "品牌甲", moved, force=True, start=project
    )
    check("当前品牌可以迁移知识库路径", migrated["kb_root"] == str(moved.resolve()))
    check("迁移后旧索引已清空", not stale.exists())
    check("迁移后保留官方白名单",
          migrated["official_sources"][0]["url"] == "https://example.com",
          str(migrated["official_sources"]))
    check("迁移明确要求重建索引", any("运行 index" in w for w in migrated_warnings),
          str(migrated_warnings))
    check("迁移保留学习目录", (project / ".blueink" / "learning").is_dir())

    parser = blueink_cli.build_parser()
    subcommands = next(action for action in parser._actions
                       if "bind" in (getattr(action, "choices", None) or {}))
    bind_flags = {
        option for action in subcommands.choices["bind"]._actions for option in action.option_strings
    }
    check("绑定命令只暴露当前产品参数", bind_flags == {
        "-h", "--help", "--project", "--json", "--brand", "--kb", "--brand-key",
        "--official", "--notes", "--create", "--force",
    }, str(sorted(bind_flags)))
    return project


def test_index_and_retrieve(project: Path) -> None:
    result = index_kb.build(start=project)
    stats = result["stats"]
    check("索引扫到了文件", stats["total"] >= 8, str(stats))
    check("顶层字段明确表示指令产物根目录",
          "instruction_artifact_roots" in result and "skill_roots" not in result,
          str(sorted(result)))

    by_path = {rec["path"]: rec for rec in result["files"]}

    # 历史技能包：整棵子树被隔离，包括 references 下的模板
    tpl = "04-经验总结原稿/brandx-pr-writer/references/press_release_templates.md"
    old_skill = "04-经验总结原稿/brandx-pr-writer/SKILL.md"
    check("技能包入口文件被标为指令产物", by_path[old_skill]["instruction_artifact"])
    check("技能包 references 下的模板也被标为指令产物", by_path[tpl]["instruction_artifact"],
          "只按文件名过滤会漏掉这一个——固定模板恰好都在这里")
    check("指令产物的品类标签被清空", by_path[old_skill]["categories"] == [])
    # 老师手写的经验总结不能被误伤
    check("老师手写的经验总结未被隔离",
          not by_path["04-经验总结原稿/客户对接经验.md"]["instruction_artifact"])

    # 内容状态
    check("PDF 只有元数据", by_path["02-原文资产/参考资料.pdf"]["content_status"] == "metadata_only")
    docx = by_path["02-原文资产/品牌红线.docx"]
    check("DOCX 抽到了正文", docx["content_status"] == "text", str(docx["content_status"]))
    check("DOCX 正文进了摘要", "品牌红线" in (docx["summary"] or ""), str(docx["summary"]))

    # 阶段判定：文件名优先于目录名
    check("终稿被判为终稿", by_path["03-初终稿对比/【终稿】上市稿.md"]["stage"] == "终稿")
    check("初稿被判为初稿", by_path["03-初终稿对比/【初稿】上市稿.md"]["stage"] == "初稿")

    # 增量：第二次全部复用
    again = index_kb.build(start=project)
    check("第二次索引全部复用", again["stats"]["reused"] == again["stats"]["total"],
          str(again["stats"]))

    # 增量的承重场景：**同长度改写并恢复原修改时间**。
    # 按"文件大小 + 修改时间"判断是否复用的增量索引会在这里静默漏更新——文件确实
    # 变了，索引却认为没变，于是检索永远拿不到新内容，而且没有任何报错。改成按内容
    # 哈希比较才抓得住。这一组检查存在的理由就是把这条边界钉死：不测同长度改写，
    # 上面那句"第二次全部复用"反而会给按 size+mtime 判断的实现发一张通行证。
    target = kb_of(project) / "02-原文资产" / "需求素材.md"
    before = target.read_text(encoding="utf-8")
    stat_before = target.stat()
    after = "# 需求素材\n本次上市交付数据与产能规划。交付 42891 辆。\n"
    check("同长度改写的夹具本身等长", len(after.encode()) == len(before.encode()),
          f"{len(after.encode())} vs {len(before.encode())}")
    target.write_text(after, encoding="utf-8")
    os.utime(target, (stat_before.st_atime, stat_before.st_mtime))
    check("恢复修改时间成功", target.stat().st_mtime == stat_before.st_mtime)

    incremental = index_kb.build(start=project)
    check("同长度改写被识别为更新", incremental["stats"]["updated"] >= 1,
          f"按 size+mtime 判断复用的实现会在这里报 updated=0：{incremental['stats']}")
    fresh = retrieve_mod.search("42891", limit=10, start=project)
    check("改写后的新内容可被检索到",
          any("42891" in (h.get("summary") or "") for h in fresh["hits"]),
          str([h["path"] for h in fresh["hits"]]))
    stale = retrieve_mod.search("31767", track="fact", limit=10, start=project)
    check("旧内容不再出现在被改写的那个文件上",
          all(h["path"] != "02-原文资产/需求素材.md" for h in stale["hits"]
              if "31767" in (h.get("summary") or "")),
          str([(h["path"], h.get("summary")) for h in stale["hits"]]))

    # --limit 是预览，不落盘
    preview = index_kb.build(limit=2, start=project)
    check("--limit 是预览", preview["preview"] is True)
    check("--limit 不写索引", index_kb.load_index(project)["stats"]["total"] == stats["total"])

    # 检索：默认排除指令产物
    hit = retrieve_mod.search("模板 结构 新闻稿", limit=10, start=project)
    paths = [h["path"] for h in hit["hits"]]
    check("检索默认不返回历史技能模板", tpl not in paths, str(paths))
    check("排除计数被报出来", hit["excluded_instruction_artifacts"] >= 2, str(hit))
    opened = retrieve_mod.search("模板 结构", limit=10,
                                 include_instruction_artifacts=True, start=project)
    check("显式审计这些技能包时能取回",
          tpl in [h["path"] for h in opened["hits"]], str([h["path"] for h in opened["hits"]]))

    # 普通目录在首次索引后才新增 SKILL.md 时，未改内容的旧模板也必须
    # 立即重新分类；不能因为文件哈希未变就沿用旧的 instruction_artifact=false。
    late_root = kb_of(project) / "04-经验总结原稿" / "late-pr-writer"
    late_template = late_root / "references" / "template.md"
    late_template.parent.mkdir(parents=True)
    late_template.write_text("# 动态隔离令牌\n新闻稿必须使用固定三段式。\n", encoding="utf-8")
    plain = index_kb.build(start=project)
    late_rel = late_template.relative_to(kb_of(project)).as_posix()
    check("新增的普通模板最初不被误伤",
          not {rec["path"]: rec for rec in plain["files"]}[late_rel]["instruction_artifact"])

    (late_root / "SKILL.md").write_text("# 后加入的历史技能包\n", encoding="utf-8")
    promoted = index_kb.build(start=project)
    promoted_by_path = {rec["path"]: rec for rec in promoted["files"]}
    check("新增 SKILL.md 后旧模板被重新分类",
          promoted_by_path[late_rel]["instruction_artifact"], str(promoted["stats"]))
    blocked = retrieve_mod.search("动态隔离令牌", limit=10, start=project)
    check("后加入技能包的旧模板默认不参与检索",
          late_rel not in [h["path"] for h in blocked["hits"]], str(blocked["hits"]))

    (late_root / "SKILL.md").unlink()
    demoted = index_kb.build(start=project)
    check("移除 SKILL.md 后旧模板恢复为普通语料",
          not {rec["path"]: rec for rec in demoted["files"]}[late_rel]["instruction_artifact"])

    # 风格轨不要初稿
    style = retrieve_mod.search("上市", track="style", limit=10, start=project)
    style_paths = [h["path"] for h in style["hits"]]
    check("风格轨排除初稿", "03-初终稿对比/【初稿】上市稿.md" not in style_paths, str(style_paths))
    check("风格轨包含终稿", "03-初终稿对比/【终稿】上市稿.md" in style_paths, str(style_paths))

    # 策略轨要初终稿对比
    strategy = retrieve_mod.search("上市", track="strategy", limit=10, start=project)
    check("策略轨返回初终稿对比",
          any("初终稿对比" in p for p in [h["path"] for h in strategy["hits"]]))

    # 检索结果透出内容状态
    check("检索结果带 content_status", all("content_status" in h for h in hit["hits"]))

    # 换电脑／移动目录后不能继续返回旧索引的貌似正常结果。
    current_kb = kb_of(project)
    hidden_kb = current_kb.with_name(current_kb.name + "-temporarily-moved")
    current_kb.rename(hidden_kb)
    try:
        expect_raises("知识库路径失效时拒绝旧索引检索", workspace.WorkspaceError,
                      retrieve_mod.search, "上市", start=project)
    finally:
        hidden_kb.rename(current_kb)

    index_path = project / ".blueink" / "index" / "index.json"
    corrupted = json.loads(index_path.read_text(encoding="utf-8"))
    corrupted["kb_root"] = str(project / "另一个知识库")
    index_path.write_text(json.dumps(corrupted, ensure_ascii=False), encoding="utf-8")
    err = expect_raises("索引根与工作空间不一致时拒绝检索", workspace.WorkspaceError,
                        retrieve_mod.search, "上市", start=project)
    check("拒绝时要求重建索引", "index --full" in str(err), str(err))
    index_kb.build(full=True, start=project)


def test_symlink_skipped(tmp: Path) -> None:
    kb = tmp / "kb_sym"
    # 带语料布局特征的目录，确保这是一个"正常单品牌库"而不是集合层
    (kb / "02-原文资产").mkdir(parents=True)
    (kb / "02-原文资产" / "a.md").write_text("正常内容", encoding="utf-8")
    (kb / "05-成品参考").mkdir(parents=True)
    (kb / "05-成品参考" / "b.md").write_text("参考稿", encoding="utf-8")
    outside = tmp / "别的品牌"
    outside.mkdir()
    (outside / "机密.md").write_text("别的品牌的稿子", encoding="utf-8")
    (kb / "越界链接").symlink_to(outside)

    project = tmp / "p_sym"
    project.mkdir()
    # 一条无关的符号链接不该凭空触发品牌集合层拦截
    workspace.bind("品牌甲", kb, start=project)
    result = index_kb.build(start=project)
    paths = [rec["path"] for rec in result["files"]]
    check("符号链接不被跟随", not any("越界链接" in p for p in paths), str(paths))
    check("跳过项被报出来", result["stats"]["skipped"] >= 1, str(result["stats"]))
    check("跳过原因写清了", any("符号链接" in s for s in result["skipped"]), str(result["skipped"]))


def allows(url: str, data: dict) -> bool:
    try:
        official_mod.check_url(url, data)
    except official_mod.OfficialSourceError:
        return False
    return True


def test_official(project: Path) -> None:
    """白名单是 example.com（RFC 2606 保留域），见 test_bind 的绑定参数。

    主机名与 scheme 分开写：被测的是主机名处理，scheme 只有 http/https 两种合法值，
    单独在 ``bad_scheme`` 里验。这样每一行都只表达一件事。

    每一条都走 ``check``，通过和失败都计数——只在失败时计数的循环，全部跳过时看起来
    和全部通过一模一样，那正是这个技能反对的"把没查伪装成查过了"。
    """
    data = workspace.load(project)
    allow_hosts = [
        "example.com",              # 主域
        "www.example.com/news",     # www 与路径
        "ir.example.com/x",         # 子域
        "EXAMPLE.COM/News",         # 大小写
        "example.com./x",           # 尾点
    ]
    deny_hosts = [
        "example.com.attacker.test/a",         # 后缀伪装：字符串包含判断会放过它
        "example.com@attacker.test/",          # 用户名伪装
        "user:pw@example.com/",                # 带凭据
        "attacker.test/https://example.com",   # 路径伪装
        "127.0.0.1/example.com",               # IP 直连
        "notexample.com",                      # 相似名
        "example.com.cn",                      # 同前缀不同 TLD
    ]
    bad_scheme = ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x"]

    for scheme in ("https://", "http://"):
        for host in allow_hosts:
            url = scheme + host
            check(f"应放行 {url}", allows(url, data))
    for host in deny_hosts:
        url = "https://" + host
        check(f"应拒绝 {url}", not allows(url, data))
    for url in bad_scheme:
        check(f"应拒绝非 http/https 的 {url}", not allows(url, data))

    # 通配符与 IP 不允许进白名单
    for bad in ("*.example.com", "127.0.0.1", ""):
        expect_raises(f"白名单不该接受 {bad!r}", official_mod.OfficialSourceError,
                      official_mod.normalize_domain, bad)

    # 白名单为空时一律拒绝，而不是一律放行
    try:
        official_mod.check_url("https://example.com", {"official_sources": []})
    except official_mod.OfficialSourceError as exc:
        check("空白名单时报错说清了怎么补", "bind" in str(exc), str(exc))
    else:
        check("空白名单应拒绝一切", False, "被放行了")


def test_memory(project: Path) -> None:
    run = run_record.open_run("生成", start=project)
    run_id = run["run_id"]
    check("新运行元数据字段完整", set(run) == set(run_record.RUN_META_FIELDS) - {"artifacts"},
          str(sorted(run)))

    good = {
        "scope": "workspace", "evidence_strength": "high",
        "knowledge": "媒体供稿把技术参数转换为可感知场景",
        "trigger": {"category": "媒体观点供稿"}, "not_applicable": ["新闻稿"],
    }
    bad = {
        "scope": "brand", "evidence_strength": "high",
        "knowledge": "开头不要写行业背景", "trigger": {"category": "新闻稿"},
    }
    result = memory_mod.add_candidates([good, bad], run_id=run_id, start=project)
    check("合格候选被接受", len(result["accepted"]) == 1, str(result))
    check("缺 not_applicable 被拒", len(result["rejected"]) == 1, str(result))
    mid = result["accepted"][0]

    stored = memory_mod.listing(start=project)
    check("列出当前品牌记忆", stored["count"] == 1, str(stored["count"]))
    check("记忆条目只返回当前字段",
          set(stored["items"][0]) <= set(memory_mod.MEMORY_FIELDS) | {"tier", "usage"},
          str(sorted(stored["items"][0])))

    # 独立事件必须指向真实运行
    exc = expect_raises("凭空的独立事件应被拒", memory_mod.MemoryError_,
                        memory_mod.reinforce, mid, run_id="根本不存在的run", start=project)
    if exc is not None:
        check("报错提示了 --same-event", "same-event" in str(exc), str(exc))
    same = memory_mod.reinforce(mid, run_id="根本不存在的run", new_event=False, start=project)
    check("同一事件的同向证据只 +0.05", abs(same["confidence"] - 0.55) < 1e-6,
          str(same["confidence"]))

    second = run_record.open_run("生成", start=project)
    boosted = memory_mod.reinforce(mid, run_id=second["run_id"], start=project)
    check("独立事件 +0.15", abs(boosted["confidence"] - 0.70) < 1e-6, str(boosted["confidence"]))
    check("独立事件计数增加", boosted["distinct_events"] == 2, str(boosted["distinct_events"]))

    # 置信度封顶 0.9，永不到 1.0
    for _ in range(4):
        extra = run_record.open_run("生成", start=project)
        capped = memory_mod.reinforce(mid, run_id=extra["run_id"], start=project)
    check("置信度封顶 0.9", capped["confidence"] <= 0.9, str(capped["confidence"]))

    # 反例降置信度并缩范围，不删旧结论
    countered = memory_mod.counterexample(mid, run_id=second["run_id"], note="核心信息稿不适用",
                                          narrow="核心信息", start=project)
    check("反例降置信度", countered["confidence"] < 0.9, str(countered["confidence"]))
    check("反例被保留", len(countered["counterexamples"]) == 1)
    check("适用范围被缩小", "核心信息" in countered["not_applicable"],
          str(countered["not_applicable"]))
    check("旧结论未被删除", countered["knowledge"].startswith("媒体供稿"))

    # methodology 级永不写入 memory.json
    routed = memory_mod.add_candidates(
        [{"scope": "methodology", "knowledge": "这类失败还没被建模", "not_applicable": ["待补充"]}],
        run_id=run_id, start=project,
    )
    check("methodology 级被分流", len(routed["routed_to_methodology"]) == 1, str(routed))
    store = json.loads((workspace.learning_dir(project) / "memory.json").read_text(encoding="utf-8"))
    check("methodology 级不在记忆库里",
          all(i.get("scope") != "methodology" for i in store["items"]))

    # 低版本状态里的非当前作用域统一收敛到工作空间，不携带额外字段进入产品输出。
    store["version"] = memory_mod.MEMORY_VERSION - 1
    store["items"].append({
        **store["items"][0], "id": "M-0000-00-00-01", "scope": "account",
        "unexpected_field": "不会进入当前 schema",
    })
    (workspace.learning_dir(project) / "memory.json").write_text(
        json.dumps(store, ensure_ascii=False), encoding="utf-8"
    )
    migrated = memory_mod.listing(start=project)
    migrated_item = next(i for i in migrated["items"] if i["id"] == "M-0000-00-00-01")
    check("低版本记忆收敛为工作空间作用域", migrated_item["scope"] == "workspace",
          str(migrated_item))
    check("低版本记忆不透传额外字段", "unexpected_field" not in migrated_item,
          str(migrated_item))

    return None


def test_audit_localises(tmp: Path) -> None:
    """审计器必须把违约定位到具体文件，否则"能定位"这句承诺是空的。"""
    run_dir = tmp / "run"
    run_dir.mkdir()
    kb = tmp / "kb_audit"
    (kb / "02-原文资产").mkdir(parents=True)
    (kb / "02-原文资产" / "真实存在.md").write_text("x", encoding="utf-8")

    (run_dir / "meta.json").write_text(json.dumps({
        "run_id": "T-1", "started_via": "/blueink-suite", "mode": "生成",
        "brand": "品牌甲", "kb_root": str(kb), "bound": True, "stage": 9,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "T-1", "task_id": "T1", "status": "ok",
        "read_paths": ["02-原文资产/真实存在.md", "02-原文资产/凭空写出的引用.md"],
        "facts": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")

    verdict = audit_mod.audit(str(run_dir))
    a2 = next(c for c in verdict["checks"] if c["id"] == "A2")
    check("A2 抓出不存在的引用", a2["status"] == "fail", str(a2))
    check("A2 指名了那个文件", "凭空写出的引用" in a2["detail"], str(a2["detail"]))
    check("每条违约都有 evidence", all(c["evidence"] for c in verdict["checks"]
                                     if c["status"] == "fail"))
    check("五项契约一条不少", [c["id"] for c in verdict["checks"]] ==
          ["A1", "A2", "A3", "A4", "A5"], str([c["id"] for c in verdict["checks"]]))
    check("结论与明细自洽", verdict["failed"] == [c["id"] for c in verdict["checks"]
                                              if c["status"] == "fail"])


def test_task_attachments(tmp: Path, project: Path) -> None:
    """老师给的附件：登记后合法，未登记则与越界读取不可区分。

    这一组锁的是一个必须避免的矛盾——主智能体允许读老师附加的跨根文件，
    审计规则却把同一个路径判为越界。两边都不肯让步，于是每次都得手工解释一遍。
    """
    outside = tmp / "老师给的附件"
    outside.mkdir()
    att = outside / "【指引】某季度财报.md"
    att.write_text("指引正文", encoding="utf-8")

    # 登记：路径、哈希、体积、归属、来源
    registered = run_record.register_attachments([str(att)], brand="品牌甲")
    check("附件登记记了绝对路径", registered[0]["path"] == str(att.resolve()), str(registered))
    check("附件登记记了内容哈希", len(registered[0]["sha256"]) == 64, str(registered))
    check("附件登记标了 source=user", registered[0]["source"] == "user", str(registered))
    expect_raises("附件不存在时直接拒绝", workspace.WorkspaceError,
                  run_record.register_attachments, [str(outside / "不存在.md")])
    expect_raises("附件给目录时直接拒绝", workspace.WorkspaceError,
                  run_record.register_attachments, [str(outside)])
    # --project 与 --json 在全局位置和子命令位置都必须生效。一个只在某个位置生效的
    # 参数等于一个会随机失败的参数——阶段指导写着"加 --json"，调用时自然写在后面。
    import subprocess as _sp
    _entry = str(Path(__file__).resolve().parent / "blueink.py")
    for _label, _argv in (("全局位置", ["--project", str(project), "--json", "status"]),
                          ("子命令位置", ["status", "--project", str(project), "--json"])):
        _r = _sp.run([sys.executable, _entry, *_argv], capture_output=True, text=True)
        check(f"--project/--json 在{_label}生效",
              _r.returncode == 0 and _r.stdout.lstrip().startswith("{"),
              f"rc={_r.returncode} out={_r.stdout[:80]} err={_r.stderr[-120:]}")

    dedup = run_record.register_attachments([str(att), str(att)], brand="品牌甲")
    check("同一附件传两次只登记一份", len(dedup) == 1, str(len(dedup)))
    expect_raises("绑定模式不接受附件", workspace.WorkspaceError,
                  run_record.open_run, "绑定", project, None, [str(att)])

    # 证据边界默认值：有附件 → attachments，无附件 → kb
    meta_a = run_record.open_run("生成", start=project, attachments=[str(att)])
    check("有附件时边界默认 attachments", meta_a["evidence_boundary"] == "attachments",
          str(meta_a.get("evidence_boundary")))
    meta_b = run_record.open_run("生成", start=project)
    check("无附件时边界默认 kb", meta_b["evidence_boundary"] == "kb",
          str(meta_b.get("evidence_boundary")))
    check("显式覆盖边界生效",
          run_record.open_run("生成", start=project, attachments=[str(att)],
                              evidence_boundary="kb")["evidence_boundary"] == "kb")

    # 审计：登记过的库外附件合法；同一个路径没登记就判违约
    kb = kb_of(project)
    for name, attachments in (("registered", [str(att)]), ("unregistered", [])):
        run_dir = tmp / f"run-att-{name}"
        run_dir.mkdir()
        (run_dir / "meta.json").write_text(json.dumps({
            "run_id": f"A-{name}", "started_via": "/blueink-suite", "mode": "生成",
            "brand": "品牌甲", "kb_root": str(kb), "bound": True, "stage": 2,
            "task_attachments": run_record.register_attachments(attachments, brand="品牌甲"),
            "evidence_boundary": "attachments" if attachments else "kb",
        }, ensure_ascii=False), encoding="utf-8")
        (run_dir / "evidence.json").write_text(json.dumps({
            "role": "evidence-researcher", "run_id": f"A-{name}", "task_id": "T1",
            "status": "ok", "read_paths": [str(att)], "facts": [], "gaps": [], "discarded": [],
        }, ensure_ascii=False), encoding="utf-8")
        a2 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A2")
        if attachments:
            check("登记过的库外附件不判违约", a2["status"] != "fail", str(a2))
        else:
            check("未登记的库外文件仍判违约", a2["status"] == "fail", str(a2))
            check("违约文案指出该先登记", "登记" in a2["detail"], str(a2["detail"]))

    # 声明以附件为准却在没有缺口时扩展检索：可见提示，但不判违约
    run_dir = tmp / "run-att-expand"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({
        "run_id": "A-expand", "started_via": "/blueink-suite", "mode": "生成",
        "brand": "品牌甲", "kb_root": str(kb), "bound": True, "stage": 2,
        "task_attachments": run_record.register_attachments([str(att)], brand="品牌甲"),
        "evidence_boundary": "attachments",
    }, ensure_ascii=False), encoding="utf-8")
    kb_file = next(p for p in kb.rglob("*.md") if p.is_file())
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "A-expand", "task_id": "T1", "status": "ok",
        "read_paths": [str(att), str(kb_file.relative_to(kb))],
        "facts": [], "gaps": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")
    a2 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A2")
    check("未申报缺口的整库扩张不判违约", a2["status"] != "fail", str(a2))
    check("未申报缺口的整库扩张给可见提示", "没有申报" in a2["detail"], str(a2["detail"]))

    # 风格样本不算扩张："以附件为准"关的是事实边界，不是表达。同一个库内文件
    # 记进 style_refs 之后，整库扩张提示就不该再点它——否则会训练出"纯附件任务
    # 一份风格样本都不敢取"，而那是唯一能拿到"这位客户实际采用过的写法"的地方。
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "A-expand", "task_id": "T1", "status": "ok",
        "read_paths": [str(att), str(kb_file.relative_to(kb))],
        "style_refs": [{"path": str(kb_file.relative_to(kb)), "why": "同品牌同场景终稿"}],
        "facts": [], "gaps": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")
    a2 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A2")
    check("记进 style_refs 的库内文件不算整库扩张", "没有申报" not in a2["detail"], str(a2["detail"]))

    # A4：程序授权的附件与写作回执写法不同（软链接前缀）时不得判越权。
    # macOS 上 /var/folders/... 与 /private/var/folders/... 是同一个目录，
    # 两侧各写一种是常态；只比原样字符串会把合法读取误判成越权。
    run_dir = tmp / "run-att-writer"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(json.dumps({
        "run_id": "A-writer", "started_via": "/blueink-suite", "mode": "生成",
        "brand": "品牌甲", "kb_root": str(kb), "bound": True, "stage": 5,
        "task_attachments": run_record.register_attachments([str(att)], brand="品牌甲"),
        "evidence_boundary": "attachments",
    }, ensure_ascii=False), encoding="utf-8")
    unresolved, resolved = str(att), str(att.resolve())
    (run_dir / "program.json").write_text(json.dumps({
        "run_id": "A-writer", "authorized_reads": [unresolved], "style_refs": [],
        "material_plan": {"discarded": []}, "assumptions": [],
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "draft.md").write_text("正文", encoding="utf-8")

    def _write_receipt(paths: list[str]) -> None:
        (run_dir / "write-receipt.json").write_text(json.dumps({
            "role": "professional-writer", "run_id": "A-writer", "task_id": "T3",
            "status": "ok", "read_paths": paths, "style_refs_used": [],
            "deviations": [], "missing_facts": [],
        }, ensure_ascii=False), encoding="utf-8")

    _write_receipt([resolved])
    a4 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A4")
    check("同一附件的两种路径写法不判越权", a4["status"] != "fail", str(a4))

    _write_receipt([resolved, str(tmp / "没授权过.md")])
    a4 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A4")
    check("真正未授权的文件仍判越权", a4["status"] == "fail", str(a4))

    # 写作者读自己的任务输入 program.json 不算越权——它就是写作者的输入。
    # 不放过这一条会制造纯误报：主智能体忘了把 program.json 写进 authorized_reads
    # 就判违约，而 A4 守的只有"写作阶段重新扫知识库"。
    _write_receipt([resolved, str(run_dir / "program.json")])
    a4 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A4")
    check("写作者读运行目录内的自身输入不判越权", a4["status"] != "fail", str(a4))

    # A2：运行目录内的上游回执不算越界。策略师读 evidence.json、核验员读
    # program.json、红队阶段读 draft.md 都是阶段执行协议规定的正常流程；判它越界会让
    # **每一次合规运行**都会在 A2 上违约。
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "A-writer", "task_id": "T1", "status": "ok",
        "read_paths": [str(att), str(run_dir / "program.json")],
        "facts": [], "gaps": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")
    a2 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A2")
    check("读运行目录内的上游回执不判越界", a2["status"] != "fail", str(a2))

    # A2：路径被写残（绝对路径掐掉前缀）要报出来，并指出成因是路径不完整。
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "A-writer", "task_id": "T1", "status": "ok",
        "read_paths": ["某个上层目录/02-原文资产/需求素材.md"],
        "facts": [], "gaps": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")
    a2 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A2")
    check("写残的路径判违约", a2["status"] == "fail", str(a2))
    check("违约文案指出路径不完整", "路径不完整" in a2["detail"], str(a2["detail"]))

    # A5：在飞运行不判违约。走到第 7 步、还没交付的运行是在飞而不是违约——
    # "只有走到了那一步却违约才判 violated"是写在文档里的口径。判据是 closed_at。
    (run_dir / "evidence.json").write_text(json.dumps({
        "role": "evidence-researcher", "run_id": "A-writer", "task_id": "T1", "status": "ok",
        "read_paths": [str(att)], "facts": [], "gaps": [], "discarded": [],
    }, ensure_ascii=False), encoding="utf-8")
    _write_receipt([resolved])
    meta_path = run_dir / "meta.json"
    base_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    for label, closed, expect_fail in (("未归档", None, False), ("已归档", "2026-08-23T12:00:00", True)):
        meta_path.write_text(json.dumps({**base_meta, "closed_at": closed}, ensure_ascii=False),
                             encoding="utf-8")
        a5 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A5")
        check(f"A5 缺 delivery.md · {label}", (a5["status"] == "fail") == expect_fail, str(a5))
    meta_path.write_text(json.dumps(base_meta, ensure_ascii=False), encoding="utf-8")

    # A5：来源清单不得引用"只在检索候选里出现、从没被打开过"的文件。
    # 候选清单是"检索返回了什么"，不是"谁打开了什么"——混同就等于放过一种凭空引用。
    ghost = "02-原文资产/只在候选里出现过.md"
    (run_dir / "retrievals.json").write_text(json.dumps([{
        "query": "x", "hits": [{"path": ghost, "score": 5, "why": "标题相关但没打开"}],
    }], ensure_ascii=False), encoding="utf-8")
    (run_dir / "verify.json").write_text(json.dumps({
        "role": "source-verifier", "run_id": "A-writer", "task_id": "T4",
        "status": "pass", "verdict": "可进入人工初审", "read_paths": [],
        "claims": [], "cross_brand": [], "redline_hits": [],
        "coverage": {"total_claims": 1, "matched": 1, "drifted": 0, "unsourced": 0, "stale": 0},
        "sources_used": [{"path_or_url": ghost, "kind": "历史稿件", "date": "2026-02-01"}],
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "delivery.md").write_text(
        "正文\n\n可进入人工初审\n\n## 实际来源\n- 某来源\n", encoding="utf-8")
    _write_receipt([resolved])
    a5 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A5")
    check("只在候选里出现的来源判违约", a5["status"] == "fail", str(a5))
    check("违约文案点名那个文件", "只在候选里出现过" in a5["detail"], str(a5["detail"]))

    (run_dir / "program.json").write_text(json.dumps({
        "run_id": "A-writer", "authorized_reads": [unresolved, ghost], "style_refs": [],
        "material_plan": {"discarded": []}, "assumptions": [],
    }, ensure_ascii=False), encoding="utf-8")
    _write_receipt([resolved, ghost])
    a5 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A5")
    check("真的被打开过的来源不判违约", a5["status"] != "fail", str(a5))
    for name in ("retrievals.json", "verify.json", "delivery.md"):
        (run_dir / name).unlink()

    # A3：零轮访谈是合法结果，不是违约。附件把交付合同和证据边界都封闭时，
    # 一个合法问题都没有才是对的；判它违约会训练出"为了过审计随便问一句"。
    # 但没有理由的零轮与"根本没访谈"在证据上不可区分，所以必须写 stopped_because。
    for label, interview, expect_fail in (
        ("零轮且写了理由", {"rounds": [], "stopped_because": "附件已封闭证据边界与交付合同，无合法问题可问"}, False),
        ("零轮但没写理由", {"rounds": []}, True),
    ):
        (run_dir / "interview.json").write_text(
            json.dumps(interview, ensure_ascii=False), encoding="utf-8")
        a3 = next(c for c in audit_mod.audit(str(run_dir))["checks"] if c["id"] == "A3")
        check(f"A3 {label}", (a3["status"] == "fail") == expect_fail, str(a3))
        if not expect_fail:
            check("A3 零轮留下可见提示", "零轮访谈" in a3["detail"], str(a3["detail"]))


def test_kb_onboarding(tmp: Path) -> None:
    """老师手上还没有知识库目录时，技能要能问出路径并建出来。

    这是首次使用的第一道门。要求老师先自己建目录、自己分类，等于把知识工程的活
    推回给他——而"不要求老师先整理知识库"是这套设计的前提。
    """
    project = tmp / "onboard"
    project.mkdir()
    target = tmp / "全新品牌知识库"

    # 目录不存在且没给 --create：拒绝，并且要告诉他有 --create 这条路。
    err = expect_raises("目录不存在时拒绝绑定", workspace.WorkspaceError,
                        workspace.bind, "全新品牌", target, start=project)
    check("拒绝时指出了 --create", "--create" in str(err), str(err))
    check("拒绝时没有建出目录", not target.exists())

    data, warnings = workspace.bind("全新品牌", target, create=True, start=project)
    check("骨架已建出", target.is_dir())
    for folder in workspace.DEFAULT_CORPUS_LAYOUT.values():
        check(f"建出 {folder}", (target / folder).is_dir())
    check("建了说明文件", (target / "README.md").is_file())
    check("配置写了语料布局", data.get("corpus_layout") == workspace.DEFAULT_CORPUS_LAYOUT,
          str(data.get("corpus_layout")))
    check("空目录不再是拒绝理由", data.get("kb_root") == str(target.resolve()))
    check("明确告知目录是空的", any("空" in w for w in warnings), str(warnings))

    # 新建出来的骨架不能被自己的品牌集合层判定误拦——五个语料目录并列，
    # 如果判成"五个兄弟品牌"，那么每次 --create 之后都要 --force 才能再绑。
    check("新建骨架不被判成品牌集合层",
          workspace.looks_like_brand_collection(target) == [],
          str(workspace.looks_like_brand_collection(target)))

    # 已存在但不是目录：不能静默把它当知识库
    afile = tmp / "这是个文件.md"
    afile.write_text("x", encoding="utf-8")
    expect_raises("路径是文件时拒绝", workspace.WorkspaceError,
                  workspace.bind, "某品牌", afile, create=True, start=tmp / "onboard2")

    moved = tmp / "被移动的品牌知识库"
    target.rename(moved)
    err = expect_raises("绑定路径失效时拒绝开启运行", workspace.WorkspaceError,
                        run_record.open_run, "生成", start=project, brand="全新品牌")
    check("路径失效错误要求重新绑定", "重新绑定" in str(err), str(err))
    import subprocess as _sp_onboarding
    status = _sp_onboarding.run(
        [sys.executable, str(Path(__file__).resolve().parent / "blueink.py"),
         "--project", str(project), "--json", "status"],
        capture_output=True, text=True,
    )
    payload = json.loads(status.stdout)
    check("路径失效重新触发资料源询问", payload.get("kb_exists") is False
          and payload.get("reference_onboarding_required") is True, str(payload))


def test_brand_mismatch(tmp: Path) -> None:
    """本次要写的品牌与绑定的知识库不是一个：必须在取证之前拦住。

    拦不住的后果是把别家客户的表达和事实带进这一稿，而这类错误在成稿里看不出来。
    """
    project = tmp / "mismatch"
    project.mkdir()
    kb = tmp / "kb_mismatch"
    (kb / "01-原文资产").mkdir(parents=True)
    (kb / "01-原文资产" / "a.md").write_text("理想 L9 上市", encoding="utf-8")
    workspace.bind("理想汽车", kb, start=project)
    ws = workspace.load(project)

    for asked, expected, why in (
        ("理想汽车", True, "同名"),
        ("理想", True, "简称"),
        ("  理想汽车 ", True, "带空格"),
        ("", True, "没声明品牌时按绑定品牌处理"),
        ("东风奕派", False, "另一个品牌"),
        ("现代汽车", False, "另一个品牌"),
    ):
        matched, reason = workspace.brand_matches(ws, asked)
        check(f"品牌判定 {asked!r}（{why}）", matched is expected, f"{matched}：{reason}")

    # 「现代中国」与「现代汽车」互不为子串：集合层名字不能被当成具体品牌。
    hyundai = {"brand": "现代中国", "brand_key": "hyundai-china"}
    matched, _ = workspace.brand_matches(hyundai, "现代汽车")
    check("现代中国 ≠ 现代汽车", matched is False)

    # open 是这条判定的强制点：不匹配就不该开出一次运行。
    err = expect_raises("品牌不匹配时拒绝开启运行", workspace.WorkspaceError,
                        run_record.open_run, "生成", start=project, brand="东风奕派")
    text = str(err)
    check("拒绝时写出了双方", "东风奕派" in text and "理想汽车" in text, text)
    check("拒绝时给了出路", "出路" in text, text)
    runs = list((project / ".blueink" / "runs").glob("*")) if (
        project / ".blueink" / "runs").is_dir() else []
    check("被拒绝的运行没有落盘", not [p for p in runs if p.is_dir()], str(runs))

    meta = run_record.open_run("生成", start=project, brand="理想")
    check("简称可以正常开启", meta["brand"] == "理想汽车", str(meta["brand"]))
    check("记下了老师本次说的品牌", meta.get("brand_asked") == "理想", str(meta.get("brand_asked")))

    namespaced = run_record.open_run(
        "定位", start=project, brand="理想汽车",
        started_via="/blueink-suite:blueink-suite",
    )
    check("Claude Code 命名空间入口被如实记录",
          namespaced["started_via"] == "/blueink-suite:blueink-suite")
    meta_path = run_record.run_dir_for(namespaced["run_id"], project) / "run.json"
    expanded = json.loads(meta_path.read_text(encoding="utf-8"))
    expanded["unexpected_field"] = "不会进入当前 schema"
    meta_path.write_text(json.dumps(expanded, ensure_ascii=False), encoding="utf-8")
    check("读取运行记录时不透传额外字段",
          "unexpected_field" not in run_record.load_meta(namespaced["run_id"], project))
    check("读取最近运行时不透传额外字段",
          "unexpected_field" not in (run_record.latest(project) or {}))
    run_record.set_stage(namespaced["run_id"], 0, start=project)
    check("写回运行记录时只保留当前字段",
          set(json.loads(meta_path.read_text(encoding="utf-8"))) <= set(run_record.RUN_META_FIELDS))
    a1 = next(c for c in audit_mod.audit(
        str(run_record.run_dir_for(namespaced["run_id"], project))
    )["checks"] if c["id"] == "A1")
    check("命名空间入口通过 A1", a1["status"] != "fail", str(a1))


def test_attachment_only_run(tmp: Path) -> None:
    """老师直接给了参考文件路径：本次不需要品牌知识库。

    首次不能因为附件静默跳过长期资料源询问；老师明确说"本次只用附件"后，仍可
    走一次性快线。这一次没有品牌库参与，必须一眼可见。
    """
    project = tmp / "attach_only"
    project.mkdir()
    (project / ".blueink").mkdir()   # 有 .blueink 但没有 workspace.yaml：未绑定

    import subprocess as _sp_status
    _entry = str(Path(__file__).resolve().parent / "blueink.py")
    status = _sp_status.run(
        [sys.executable, _entry, "--project", str(project), "--json", "status"],
        capture_output=True, text=True,
    )
    status_payload = json.loads(status.stdout)
    check("未绑定 status 是正常状态", status.returncode == 0 and status_payload["bound"] is False,
          f"rc={status.returncode} out={status.stdout[:120]} err={status.stderr[-120:]}")
    check("未绑定状态要求首次资料源询问",
          status_payload.get("reference_onboarding_required") is True
          and "共同根目录" in str(status_payload.get("question")), str(status_payload))

    ref = tmp / "refs"
    ref.mkdir()
    one = ref / "指引.md"
    one.write_text("【指引】本次传播要点", encoding="utf-8")

    err = expect_raises("未绑定且无附件时拒绝", workspace.WorkspaceError,
                        run_record.open_run, "生成", start=project)
    check("无附件拒绝时要求历史稿件根目录", "共同根目录" in str(err), str(err))

    err = expect_raises("未绑定带附件仍先问资料源", workspace.WorkspaceError,
                        run_record.open_run, "生成", start=project, brand="某新品牌",
                        attachments=[str(one)])
    check("附件不能静默绕过首次询问", "本次只用附件" in str(err), str(err))

    meta = run_record.open_run("生成", start=project, brand="某新品牌",
                               attachments=[str(one)], one_off=True)
    check("老师确认 one-off 后可以开启", meta["run_id"])
    check("证据边界强制为附件", meta["evidence_boundary"] == "attachments",
          str(meta["evidence_boundary"]))
    check("记下了本次品牌", meta["brand"] == "某新品牌", str(meta["brand"]))
    check("kb_root 为空", meta["kb_root"] == "", str(meta["kb_root"]))
    check("附件带了哈希", (meta["task_attachments"] or [{}])[0].get("sha256"))

    # 未绑定时声明 kb 边界是一句假话：没有 kb_root，下游会以为有库可查。
    expect_raises("未绑定时不许声明 kb 边界", workspace.WorkspaceError,
                  run_record.open_run, "生成", start=project,
                  attachments=[str(one)], evidence_boundary="kb", one_off=True)
    expect_raises("one-off 不能脱离附件使用", workspace.WorkspaceError,
                  run_record.open_run, "生成", start=project, one_off=True)

    parser = blueink_cli.build_parser()
    subcommands = next(action for action in parser._actions
                       if "open" in (getattr(action, "choices", None) or {}))
    open_flags = {
        option for action in subcommands.choices["open"]._actions for option in action.option_strings
    }
    check("open 暴露显式 one-off 出口", "--one-off" in open_flags, str(sorted(open_flags)))

    # 当前快线：未绑定运行里，只有登记过的附件算合法来源，其余在写核验时拒绝。
    run_dir = run_record.run_dir_for(meta["run_id"], project)
    outside = tmp / "别人的库" / "东风奕派" / "稿子.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("奕派 eπ008", encoding="utf-8")
    run_record.save_decision(meta["run_id"], {
        "interview": {
            "rounds": [{"n": 1, "kind": "direction", "question": "按推荐写，可以吗？",
                        "answer": "可以", "changed": "确认方向"}],
            "stopped_because": "下一问不会改变传播主线",
        },
        "direction": {
            "options": [{"id": "A", "name": "推荐", "claim": "按指引成稿",
                         "suitable_for": "媒体", "cost": "弱化背景"}],
            "selected": "A", "confirmed_by_user": True,
        },
    }, start=project)
    (run_dir / "delivery.md").write_text("正文", encoding="utf-8")
    run_record.handoff_delivery(meta["run_id"], start=project)
    err = expect_raises(
        "附件模式下越界照样抓得到", ValueError, run_record.save_verify,
        meta["run_id"],
        {"issues": [], "sources_used": [{"path_or_url": str(outside), "kind": "外部"}]},
        start=project,
    )
    check("越界报的是那个库外文件", "稿子.md" in str(err), str(err))
    check("登记过的附件仍可用于核验", run_record._allowed_source(str(one), meta))


def test_attachment_delivery_first(tmp: Path) -> None:
    """附件快线先交唯一正文，再用轻量问题核验；交付后正文不被自动覆盖。"""
    project = tmp / "delivery_first"
    project.mkdir()
    (project / ".blueink").mkdir()
    kb = tmp / "delivery-first-kb"
    style = kb / "05-成品参考" / "历史终稿.md"
    style.parent.mkdir(parents=True)
    style.write_text("# 历史终稿\n\n克制、直接地交代经营结果。", encoding="utf-8")
    workspace.bind("品牌甲", kb, start=project)
    source = tmp / "delivery-first-source.md"
    source.write_text(
        "2026年一季度交付10万辆，位居细分市场第一。", encoding="utf-8"
    )
    meta = run_record.open_run(
        "生成", start=project, brand="品牌甲", attachments=[str(source)]
    )
    decision = {
        "interview": {
            "rounds": [{
                "n": 1,
                "kind": "direction",
                "question": "这一稿按销量韧性主线写，可以吗？",
                "answer": "可以",
                "changed": "确认销量韧性主线",
            }],
            "stopped_because": "下一问不会改变传播主线与信息权重",
        },
        "direction": {
            "options": [{
                "id": "A",
                "name": "销量韧性",
                "claim": "交付结果验证经营韧性",
                "suitable_for": "行业媒体",
                "cost": "弱化产品细节",
            }],
            "selected": "A",
            "confirmed_by_user": True,
        },
        "style_refs": [{
            "path": "05-成品参考/历史终稿.md",
            "why": "同品牌同品类已确认终稿",
            "caveat": "只参考表达节奏，不沿用其中事实",
        }],
    }
    outside_style = tmp / "其他品牌" / "终稿.md"
    outside_style.parent.mkdir()
    outside_style.write_text("其他品牌稿件", encoding="utf-8")
    invalid = dict(decision)
    invalid["style_refs"] = [{"path": str(outside_style), "why": "错误引用"}]
    expect_raises("风格参考不能越过单品牌根", ValueError, run_record.save_decision,
                  meta["run_id"], invalid, start=project)
    saved = run_record.save_decision(meta["run_id"], decision, start=project)
    check("附件快线保存轻量方向与风格参考", (
        saved["decision"].get("path") == "attachment-delivery-first"
        and saved["decision"]["style_refs"][0]["path"] == "05-成品参考/历史终稿.md"
    ), str(saved["decision"]))
    run_path = run_record.run_dir_for(meta["run_id"], project) / "run.json"
    valid_run = json.loads(run_path.read_text(encoding="utf-8"))
    tampered = json.loads(json.dumps(valid_run, ensure_ascii=False))
    tampered["decision"]["style_refs"][0]["path"] = str(outside_style)
    run_path.write_text(json.dumps(tampered, ensure_ascii=False), encoding="utf-8")
    a2 = next(c for c in audit_mod.audit(str(run_path.parent))["checks"] if c["id"] == "A2")
    check("手改运行记录也不能让风格参考越过品牌根", a2["status"] == "fail", str(a2))
    run_path.write_text(json.dumps(valid_run, ensure_ascii=False), encoding="utf-8")
    check("附件快线不在正文前造事实原子", saved["facts"] == [])

    run_dir = run_record.run_dir_for(meta["run_id"], project)
    delivery_path = run_dir / "delivery.md"
    delivery_text = "# 稿件\n\n2026年一季度交付10万辆，位居细分市场第一。这说明经营韧性得到验证。\n"
    delivery_path.write_text(delivery_text, encoding="utf-8")
    handed = run_record.handoff_delivery(meta["run_id"], start=project)
    check("handoff 返回完整可修改稿件", "经营韧性" in handed["delivery"])
    check("handoff 记录方向到稿件耗时", handed["metrics"].get("direction_to_delivery_seconds") is not None)
    check("handoff 自动提取高风险句", any(
        "比较范围" in item["signals"] for item in handed["risk_sentences"]
    ), str(handed["risk_sentences"]))
    import subprocess as _sp_handoff
    entry = str(Path(__file__).resolve().parent / "blueink.py")
    cli = _sp_handoff.run(
        [sys.executable, entry, "--project", str(project), "handoff", "--run", meta["run_id"]],
        capture_output=True, text=True,
    )
    check("handoff 命令会立即展示完整稿件", cli.returncode == 0 and "经营韧性" in cli.stdout,
          f"rc={cli.returncode} out={cli.stdout[-160:]} err={cli.stderr[-160:]}")

    before = delivery_path.read_bytes()
    verify = run_record.save_verify(
        meta["run_id"],
        {"issues": [], "cross_brand": [], "redline_hits": [], "editorial_risks": []},
        start=project,
    )
    check("轻量核验不要求手抄 matched claims", verify["review_mode"] == "issues-only-after-handoff")
    check("轻量核验复用已登记附件", verify["sources_used"][0]["path_or_url"] == str(source.resolve()))
    check("核验没有覆盖老师稿件", delivery_path.read_bytes() == before)
    verify = run_record.save_verify(
        meta["run_id"],
        {"issues": [{
            "quote": "经营韧性得到验证",
            "judgement": "drifted",
            "action": "建议改为体现经营韧性",
        }]},
        start=project,
    )
    check("轻量问题用短片段定位并保存完整原句", (
        verify["claims"][0]["quote"].endswith("经营韧性得到验证。")
    ), str(verify["claims"]))
    check("核验记录稿件到核验耗时", (
        run_record.load_meta(meta["run_id"], project).get("metrics") or {}
    ).get("delivery_to_verify_seconds") is not None)
    run_record.handoff_delivery(meta["run_id"], start=project)
    check("重复展示稿件不会把核验阶段倒退", (
        run_record.load_meta(meta["run_id"], project).get("stage") == 6
    ))

    closed = _sp_handoff.run(
        [sys.executable, entry, "--project", str(project), "close", "--run", meta["run_id"]],
        capture_output=True, text=True,
    )
    delivery_check = run_dir / "delivery-check.md"
    check("快线完成四份产物并保持唯一正文", (
        closed.returncode == 0
        and delivery_path.is_file()
        and delivery_check.is_file()
        and "交付核对卡" not in delivery_path.read_text(encoding="utf-8")
        and "交付核对卡" in delivery_check.read_text(encoding="utf-8")
        and "## 风格参考" in delivery_check.read_text(encoding="utf-8")
        and "历史终稿.md" in delivery_check.read_text(encoding="utf-8")
        and "经营韧性" in closed.stdout
    ), f"rc={closed.returncode} out={closed.stdout[-200:]} err={closed.stderr[-160:]}")
    check("正文快线通过五项审计", audit_mod.audit(str(run_dir))["verdict"] == "pass")

    delivery_path.write_text(delivery_text + "老师新增一句。\n", encoding="utf-8")
    err = expect_raises(
        "核验后正文变化时拒绝套旧结论",
        ValueError,
        run_record.build_delivery_check,
        meta["run_id"],
        start=project,
    )
    check("正文变化错误说明不覆盖老师", "旧核验结论" in str(err), str(err))


def test_schema_4_run_compatibility(tmp: Path) -> None:
    """产物合同升级不能让现有 schema 4 运行被误判或被新版命令改写。"""
    run_id = "20260825-000000-legacy"
    run_dir = tmp / ".blueink" / "runs" / run_id
    run_dir.mkdir(parents=True)
    source = tmp / "schema-4-source.md"
    source.write_text("来源", encoding="utf-8")
    draft = run_dir / "draft.md"
    draft.write_text("# 历史正文\n", encoding="utf-8")
    record = {
        "schema_version": 4,
        "run_id": run_id,
        "started_via": "/blueink-suite",
        "started_at": "2026-08-25T00:00:00",
        "mode": "生成",
        "brand": "品牌甲",
        "brand_key": "brand",
        "brand_asked": "品牌甲",
        "kb_root": "",
        "bound": False,
        "task_attachments": run_record.register_attachments([str(source)], brand="品牌甲"),
        "evidence_boundary": "attachments",
        "interview": {"rounds": [{
            "n": 1, "kind": "direction", "question": "按推荐写，可以吗？",
            "answer": "可以", "changed": "确认方向",
        }]},
        "direction": {"confirmed_by_user": True},
        "facts": [],
        "decision": {"path": "attachment-draft-first"},
        "metrics": {"draft_sha256": run_record._sha256(draft)},
        "stage": 9,
        "stage_name": "运行归档",
        "closed_at": "2026-08-25T00:01:00",
        "artifacts": ["delivery.md", "draft.md", "run.json", "verify.json"],
    }
    (run_dir / "run.json").write_text(
        json.dumps(record, ensure_ascii=False), encoding="utf-8"
    )
    (run_dir / "verify.json").write_text(json.dumps({
        "role": "single-agent-verifier",
        "run_id": run_id,
        "verdict": "可进入人工初审",
        "claims": [],
        "coverage": {"matched": 0, "drifted": 0, "unsourced": 0, "stale": 0},
        "risk_sentences": [],
        "draft_sha256": run_record._sha256(draft),
        "cross_brand": [],
        "redline_hits": [],
        "editorial_risks": [],
        "sources_used": [{"path_or_url": str(source), "kind": "需求材料", "date": ""}],
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "delivery.md").write_text(
        "# 历史正文\n\n交付前核对卡\n\n可进入人工初审。\n\n实际来源\n\n- 需求材料\n",
        encoding="utf-8",
    )
    check("schema 4 历史运行继续通过原合同审计", audit_mod.audit(run_dir)["verdict"] == "pass")
    err = expect_raises(
        "schema 4 历史运行保持只读", ValueError,
        run_record.handoff_delivery, run_id, start=tmp,
    )
    check("旧运行拒绝新版写入", "只读兼容" in str(err), str(err))


def _interview_run(tmp: Path, name: str, interview: dict) -> dict:
    """造一个只有 meta 与 interview.json 的运行目录，用来单测 A3。"""
    run_dir = tmp / name
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "meta.json").write_text(json.dumps({
        "run_id": name, "started_via": "/blueink-suite", "mode": "生成",
        "brand": "品牌甲", "kb_root": "", "bound": True, "stage": 1,
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "interview.json").write_text(
        json.dumps(interview, ensure_ascii=False), encoding="utf-8")
    verdict = audit_mod.audit(str(run_dir))
    return next(c for c in verdict["checks"] if c["id"] == "A3")


def test_interview_sufficiency(tmp: Path) -> None:
    """动态充分性必须是可核对的判断，不是一句"我觉得够了"。"""
    base = tmp / "a3"

    # 停止理由不指名维度：对任何一次访谈都成立，因此什么都没约束住。
    a3 = _interview_run(base, "vague", {
        "rounds": [{"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体",
                    "changed": "确定了读者"}],
        "stopped_because": "信息已经足够，可以开始写了",
    })
    check("不指名维度的停止理由判违约", a3["status"] == "fail", str(a3))
    check("违约说明列出了五个维度", "表达边界" in a3["detail"], str(a3["detail"]))

    # 五个维度都要认得，包括新加的表达边界
    for dim, reason in (
        ("事实安全", "剩余未知不影响任何事实的来源与时效"),
        ("传播主线", "下一问不会改变主线"),
        ("信息权重", "笔墨分配已经定了"),
        ("表达边界", "发言人身份与语义温度已由老师确认"),
        ("交付可行性", "篇幅与形态老师已给定"),
    ):
        a3 = _interview_run(base, f"dim-{dim}", {
            "rounds": [{"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体",
                        "changed": "确定了读者"}],
            "stopped_because": reason,
        })
        check(f"认得维度「{dim}」", a3["status"] != "fail" and dim in a3["detail"],
              f"{a3['status']}：{a3['detail']}")

    # 零轮访谈同样要指名维度
    a3 = _interview_run(base, "zero-vague", {
        "rounds": [], "stopped_because": "没什么要问的"})
    check("零轮不指名维度也判违约", a3["status"] == "fail", str(a3))

    # 同一个问题问两遍：老师会开始敷衍，而敷衍的回答比没有回答更危险
    a3 = _interview_run(base, "dup", {
        "rounds": [
            {"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体", "changed": "定了读者"},
            {"n": 2, "question": "这一稿，面向谁？", "answer": "行业媒体", "changed": "无"},
        ],
        "stopped_because": "下一问不会改变主线",
    })
    check("重复提问判违约", a3["status"] == "fail", str(a3))
    check("重复提问指出了轮次", "第 1 轮与第 2 轮" in a3["detail"], str(a3["detail"]))

    # 同一话题的不同追问不算重复——那类追问恰恰是访谈该做的事
    a3 = _interview_run(base, "followup", {
        "rounds": [
            {"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体", "changed": "定了读者"},
            {"n": 2, "question": "行业媒体里更看重技术还是市场？", "answer": "技术",
             "changed": "定了信息重心"},
        ],
        "stopped_because": "下一问不会改变主线与信息权重",
    })
    check("同话题追问不判重复", a3["status"] != "fail", str(a3))

    # 伪精确的信心百分比
    for blob in ("当前理解信心 87%", "把握：0.92", "confidence: 95%"):
        a3 = _interview_run(base, f"conf-{abs(hash(blob)) % 9999}", {
            "rounds": [{"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体",
                        "changed": "定了读者"}],
            "stopped_because": f"下一问不会改变主线。{blob}",
        })
        check(f"伪精确信心判违约（{blob}）", a3["status"] == "fail", str(a3))

    # 记忆的 confidence 是有算术定义的排序信号，不该被这条检查误伤
    a3 = _interview_run(base, "real-conf", {
        "rounds": [{"n": 1, "question": "这一稿面向谁？", "answer": "行业媒体",
                    "changed": "定了读者"}],
        "stopped_because": "下一问不会改变主线",
        "memories_used": [{"id": "M-1", "confidence": 0.82}],
    })
    check("记忆置信度不被误判成伪精确信心", a3["status"] != "fail", str(a3))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="blueink-state-"))
    try:
        kb = tmp / "kb"
        build_kb(kb)
        test_cross_platform_config()
        project = test_bind(tmp, kb)
        test_index_and_retrieve(project)
        test_symlink_skipped(tmp)
        test_official(project)
        test_memory(project)
        test_audit_localises(tmp)
        test_task_attachments(tmp, project)
        test_kb_onboarding(tmp)
        test_brand_mismatch(tmp)
        test_attachment_only_run(tmp)
        test_attachment_delivery_first(tmp)
        test_schema_4_run_compatibility(tmp)
        test_interview_sufficiency(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if FAILURES:
        print(f"状态层测试：{CHECKED} 项检查，失败 {len(FAILURES)} 项")
        for item in FAILURES:
            print(f"  ✗ {item}")
        return 1
    print(f"状态层测试：{CHECKED} 项检查全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
