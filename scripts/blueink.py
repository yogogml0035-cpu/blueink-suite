#!/usr/bin/env python3
"""BlueInk 的唯一命令入口：确定性状态与记账层。

技能正文是散文，靠模型自己记住"先跑哪个脚本、再跑哪个"是不可靠的。所以确定性
工作全部收在这一个入口里：主智能体只需要记住 ``blueink.py <子命令>``。

**这不是一条管线。** 它不按顺序驱动流程，也没有"跑完就出稿"的命令。真正的流程由
主智能体按访谈结果和证据缺口分支；这里只负责那些不该交给模型判断的事——路径归属、
哈希、增量索引、URL 白名单、运行记账、置信度算术、契约审计。

子命令：

    bind         绑定单品牌单老师工作空间（首次；--create 可顺带建知识库骨架）
    status       看当前绑定
    check-brand  本次要写的品牌与当前知识库是否匹配
    index        建立或增量更新旁路索引
    retrieve     按任务检索证据切片
    official     官方来源白名单：查看、追加、访问前校验 URL
    open         开启一次运行，拿到 run_id 与启动回执行
    stage        标记运行走到了哪一步
    close        归档一次运行
    purge        按留存策略清理旧运行记录
    memory       条件化记忆的读写
    audit        六项验收契约的机械审计
    doctor       一条命令看清当前状态

所有命令都支持 ``--json`` 输出，方便子智能体直接消费。

这里**没有**改稿编排与版本回退命令，这是边界不是遗漏：一次生成内部的回退由回执
``status`` 驱动（见《编排协议》标准流程），老师后续的修改意见只进入学习外循环。
"改稿退到哪一层"是另一个产品，第一版不做。self_check.py --claims 会双向核对这份
子命令清单与文档，多一个未声明的命令就判失败。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import audit as audit_mod
import index_kb
import memory as memory_mod
import official as official_mod
import retrieve as retrieve_mod
import run_record
import workspace


def _emit(data: Any, as_json: bool, lines: list[str] | None = None) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        for line in lines or []:
            print(line)


# --- 子命令 -----------------------------------------------------------------


def cmd_bind(args: argparse.Namespace) -> int:
    data, warnings = workspace.bind(
        args.brand,
        args.kb,
        teacher=args.teacher or "",
        brand_key=args.brand_key,
        official_urls=args.official or [],
        notes=args.notes or "",
        force=args.force,
        create=args.create,
        start=args.project,
    )
    payload = {"workspace": data, "warnings": warnings,
               "file": str(workspace.workspace_file(args.project))}
    _emit(
        payload,
        args.json,
        [
            f"已绑定：{data['brand']}（{data['brand_key']}）"
            f"｜文案老师：{data.get('teacher') or '未记名'}",
            f"知识库：{data['kb_root']}",
            f"官方来源白名单：{'、'.join(workspace.official_hosts(data)) or '（空，联网取证前必须先补）'}",
            f"配置写入：{payload['file']}",
            *[f"⚠ {w}" for w in warnings],
            "下一步：blueink.py index",
        ],
    )
    return 0


def cmd_check_brand(args: argparse.Namespace) -> int:
    """本次要写的品牌与当前绑定的知识库是否匹配。

    主智能体在访谈里确认了本次品牌之后调这一条，而不是等到取证时才发现检索命中的
    全是别家客户的稿件。未绑定不是错误——它只说明本次要么先绑定，要么以附件为准。
    """
    if not workspace.is_bound(args.project):
        payload = {"bound": False, "matched": None, "brand_asked": args.brand,
                   "why": "当前项目还没有绑定任何品牌知识库"}
        _emit(payload, args.json, [
            f"— 当前项目未绑定知识库，无法与「{args.brand}」比对。",
            "两条出路：绑定这个品牌的知识库（bind，还没有目录时加 --create），",
            "或本次只参考指定文件（open --attach <文件绝对路径>），不用知识库。",
        ])
        return 1
    data = workspace.load(args.project)
    matched, why = workspace.brand_matches(data, args.brand)
    payload = {"bound": True, "matched": matched, "brand_bound": data.get("brand"),
               "brand_asked": args.brand, "kb_root": data.get("kb_root"), "why": why}
    if matched:
        _emit(payload, args.json, [f"✓ {why}｜知识库：{data.get('kb_root')}"])
        return 0
    _emit(payload, args.json, [
        f"✗ {why}。",
        "用当前知识库给另一个品牌写稿，会把别家客户的表达和事实带进这一稿，"
        "而这类错误在成稿里看不出来。",
        f"请向老师确认：这一稿到底是「{data.get('brand')}」的，"
        f"还是要换到「{args.brand}」的知识库？",
    ])
    return 1



def cmd_status(args: argparse.Namespace) -> int:
    if not workspace.is_bound(args.project):
        payload = {"bound": False,
                   "hint": "先运行 bind --brand <品牌> --teacher <文案老师> --kb <知识库目录>"
                           "（这个品牌还没有知识库目录时加 --create）"}
        _emit(payload, args.json, [
            "当前项目未绑定品牌知识库。",
            payload["hint"],
            "只想参考几份指定文件、本次不用知识库：open --attach <文件绝对路径>。",
        ])
        return 1
    data = workspace.load(args.project)
    root = Path(str(data["kb_root"]))
    project = workspace.project_root(args.project)
    payload = {"bound": True, "project_root": str(project), "workspace": data,
               "kb_exists": root.is_dir(),
               "official_hosts": workspace.official_hosts(data)}
    _emit(
        payload,
        args.json,
        [
            f"项目根：{project}",
            f"品牌：{data['brand']}（{data['brand_key']}）",
            f"文案老师：{data.get('teacher') or '未记名（记忆归属无法区分，建议重新绑定）'}",
            f"知识库：{data['kb_root']}" + ("" if root.is_dir() else "  ⚠ 目录已失效，需要重新绑定"),
            f"官方来源：{'、'.join(payload['official_hosts']) or '（空）'}",
            f"绑定时间：{data.get('bound_at')}",
            f"备注：{data.get('notes') or '（无）'}",
        ],
    )
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    result = index_kb.build(full=args.full, limit=args.limit, start=args.project)
    stats = result["stats"]
    if args.limit:
        _emit(
            result if args.json else {"stats": stats, "preview": True},
            args.json,
            [
                f"试跑预览：只扫了前 {args.limit} 个文件，**没有写入索引**。",
                f"其中新增 {stats['added']}，可复用 {stats['reused']}，"
                f"低置信度 {stats['low_confidence']}。",
                "确认无误后去掉 --limit 再跑一次。",
            ],
        )
        return 0
    try:
        memory_mod.decay(start=args.project)
    except memory_mod.MemoryError_:
        pass  # 记忆库损坏不该阻断索引；doctor 会报出来
    lines = [
        f"索引完成：{stats['total']} 个文件（新增 {stats['added']}，更新 {stats['updated']}，"
        f"复用 {stats['reused']}，移除 {stats['removed']}）",
        f"可读正文：{stats['extractable']}；只有元数据：{stats['metadata_only']}"
        f"（这些文件的正文没被读过，不能当成已检索）",
        f"低置信度：{stats['low_confidence']}；未归类品类：{stats['uncategorized']}",
    ]
    if stats["instruction_artifacts"]:
        lines.append(
            f"⚠ 隔离历史技能产物 {stats['instruction_artifacts']} 个"
            f"（来自 {'、'.join(result.get('skill_roots') or []) or '知识库根'}）："
            f"默认不参与检索，避免外部提示词与固定模板接管本次判断。"
        )
    if stats["skipped"]:
        lines.append(f"⚠ 跳过 {stats['skipped']} 项：" + "；".join((result.get("skipped") or [])[:3]))
    lines.append("低置信度文件保留多个候选标签，不强行归类——只有影响当前稿件时才在访谈里问。")
    _emit(result if args.json else {"stats": stats, "skipped": result.get("skipped")}, args.json, lines)
    return 0


def cmd_retrieve(args: argparse.Namespace) -> int:
    result = retrieve_mod.search(
        args.query or "",
        category=args.category,
        track=args.track,
        limit=args.limit,
        since=args.since,
        loose=args.loose,
        include_instruction_artifacts=args.include_instruction_artifacts,
        start=args.project,
    )
    if args.run:
        result["recorded_to"] = retrieve_mod.record_to_run(result, args.run, start=args.project)
    lines = [f"命中 {len(result['hits'])} / 候选 {result.get('candidates_total', 0)}"]
    for hit in result["hits"]:
        flag = "" if hit.get("content_status") == "text" else f" ⚠{hit.get('content_status')}"
        lines.append(
            f"  [{hit['score']}] {hit['path']}  ({'/'.join(hit.get('evidence_type') or [])}"
            f" · {hit.get('stage')} · {hit.get('date') or '无日期'}{flag})"
        )
    if result.get("excluded_instruction_artifacts"):
        lines.append(
            f"（已排除 {result['excluded_instruction_artifacts']} 个历史技能产物；"
            f"确要审计这些技能包时加 --include-instruction-artifacts）"
        )
    if result.get("note"):
        lines.append(result["note"])
    _emit(result, args.json, lines)
    return 0


def cmd_official(args: argparse.Namespace) -> int:
    """官方来源白名单。``check-url`` 是联网取证前必须过的那道闸。"""
    data = workspace.load(args.project)
    if args.action == "list":
        hosts = workspace.official_hosts(data)
        _emit(
            {"brand": data["brand"], "hosts": hosts},
            args.json,
            [f"官方来源白名单（{len(hosts)} 条）："] + [f"  {h}" for h in hosts]
            or ["白名单为空。联网取证前先在访谈里问清官网，再 bind --official 写进来。"],
        )
        return 0

    if args.action == "add":
        if not args.url:
            print("official add 需要 --url <官网地址>", file=sys.stderr)
            return 2
        merged = [item["url"] for item in data.get("official_sources") or []] + list(args.url)
        updated, warnings = workspace.bind(
            str(data["brand"]), str(data["kb_root"]),
            teacher=str(data.get("teacher") or ""),
            brand_key=str(data.get("brand_key") or ""),
            official_urls=merged,
            corpus_layout=data.get("corpus_layout") or {},
            notes=str(data.get("notes") or ""),
            force=True, start=args.project,
        )
        _emit(
            {"hosts": workspace.official_hosts(updated), "warnings": warnings},
            args.json,
            [f"白名单已更新：{'、'.join(workspace.official_hosts(updated))}"],
        )
        return 0

    # check-url
    try:
        verdict = official_mod.check_url(args.url[0] if args.url else "", data)
    except official_mod.OfficialSourceError as exc:
        payload = {"allowed": False, "url": (args.url or [""])[0], "why": str(exc)}
        _emit(payload, args.json, [f"✗ 拒绝访问：{exc}"])
        return 1
    _emit(verdict, args.json,
          [f"✓ 允许访问：{verdict['host']}（匹配白名单 {verdict['matched_domain']}）"])
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    meta = run_record.open_run(
        args.mode,
        start=args.project,
        attachments=args.attach or [],
        evidence_boundary=args.evidence_boundary,
        brand=args.brand,
        started_via=args.started_via,
    )
    lines = [meta["launch_receipt"],
             f"运行目录：{run_record.run_dir_for(meta['run_id'], args.project)}"]
    if not meta.get("bound"):
        lines.append(
            "本次没有品牌知识库参与：证据边界就是下面这几份文件，"
            "取证不检索任何目录，也不做跨品牌实体比对。"
        )
    attachments = meta.get("task_attachments") or []
    if attachments:
        lines.append(f"本次附件 {len(attachments)} 份（证据边界：{meta['evidence_boundary']}）：")
        lines += [f"  {a['path']}  sha256:{a['sha256'][:12]}" for a in attachments]
        if meta["evidence_boundary"] == "attachments":
            # 提示按模式给。学习模式根本不成稿，对它说"先只用附件成稿"是一条
            # 会被照着执行的错话——附件在那里是归因证据，不是写作素材。
            if meta.get("mode") == "学习":
                lines.append("  → 附件是本次归因证据；哈希已记录，事后可确认比对的是哪一版")
            elif meta.get("bound"):
                lines.append("  → 先只用附件成稿；只有会阻止成稿的高影响缺口才最小范围查库")
            else:
                lines.append("  → 只用这几份成稿；缺到无法成稿时向老师要，不要自己找")
    _emit(meta, args.json, lines)
    return 0


def cmd_stage(args: argparse.Namespace) -> int:
    meta = run_record.set_stage(args.run, args.to, start=args.project)
    _emit(meta, args.json, [f"{args.run} → 阶段 {meta['stage']}（{meta['stage_name']}）"])
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    meta = run_record.close_run(args.run, start=args.project)
    _emit(
        meta,
        args.json,
        [
            f"{args.run} 已归档，产出 {len(meta.get('artifacts') or [])} 个文件",
            "建议接着跑：blueink.py audit --input "
            f"{run_record.run_dir_for(args.run, args.project)}",
        ],
    )
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    result = run_record.purge(
        keep_days=args.keep_days, keep_runs=args.keep_runs,
        apply=args.apply, start=args.project,
    )
    verb = "已删除" if args.apply else "将删除（试运行，加 --apply 才真的删）"
    lines = [
        f"留存策略：保留最近 {result['keep_runs']} 次运行、{result['keep_days']} 天内的运行",
        f"{verb} {len(result['purged'])} 次运行，保留 {result['kept']} 次",
    ]
    lines += [f"  - {r['run_id']}（{r['reason']}）" for r in result["purged"]]
    if not args.apply and result["purged"]:
        lines.append("确认无误后重跑并加 --apply。")
    _emit(result, args.json, lines)
    return 0


def cmd_memory(args: argparse.Namespace) -> int:
    action = args.action
    # 记忆归属默认跟当前工作空间登记的老师走，除非显式指定
    owner = args.teacher
    if owner is None and workspace.is_bound(args.project):
        owner = str(workspace.load(args.project).get("teacher") or "") or None

    if action == "list":
        result = memory_mod.listing(
            brand=args.brand, teacher=owner, scope=args.scope,
            min_confidence=args.min_confidence,
            include_retired=args.include_retired, start=args.project,
        )
        lines = [f"共 {result['count']} 条（老师：{result['teacher']}，"
                 f"待确认 {len(result['pending_confirmation'])}，"
                 f"方法论候选 {result['methodology_candidates']}）"]
        for item in result["items"]:
            lines.append(
                f"  [{item['confidence']:.2f} {item['tier']}] {item['id']} {item['scope']}"
                f" · {item['knowledge'][:60]}"
            )
        if result.get("other_teachers"):
            lines.append(
                f"⚠ 这个项目里还有其他老师的记忆（{'、'.join(result['other_teachers'])}），"
                f"已排除在外。一个工作空间只服务一位老师——换人请新建项目目录。"
            )
        _emit(result, args.json, lines)
        return 0

    if action == "add":
        if args.file:
            data = json.loads(Path(args.file).read_text(encoding="utf-8"))
            candidates = data.get("candidates") or []
            run_id = args.run or data.get("run_id")
            brand = args.brand or data.get("brand")
            result = memory_mod.add_candidates(
                candidates, brand=brand, teacher=owner, run_id=run_id, start=args.project
            )
        elif args.note:
            result = memory_mod.add_note(
                args.scope or "session", args.note, brand=args.brand, teacher=owner,
                run_id=args.run, start=args.project,
            )
        else:
            print("add 需要 --file <feedback.json> 或 --note <一句话>", file=sys.stderr)
            return 2
        lines = [f"接受 {len(result['accepted'])} 条，"
                 f"转入方法论候选 {len(result['routed_to_methodology'])} 条，"
                 f"拒绝 {len(result['rejected'])} 条（归属：{result['teacher']}）"]
        lines += [f"  ✗ {r['id']}：{r['why']}" for r in result["rejected"]]
        _emit(result, args.json, lines)
        return 0

    if action == "confirm":
        item = memory_mod.confirm(args.id, start=args.project)
        _emit(item, args.json, [f"{item['id']} 已生效（置信度 {item['confidence']:.2f}）"])
        return 0

    if action == "counter":
        item = memory_mod.counterexample(
            args.id, run_id=args.run or "", note=args.note or "", narrow=args.narrow or "",
            start=args.project,
        )
        _emit(item, args.json,
              [f"{item['id']} 记录反例，置信度降为 {item['confidence']:.2f}（旧结论保留）"])
        return 0

    if action == "cancel":
        item = memory_mod.cancelled(args.id, run_id=args.run or "", start=args.project)
        _emit(item, args.json, [f"{item['id']} 本次取消，置信度降为 {item['confidence']:.2f}"])
        return 0

    if action == "reinforce":
        item = memory_mod.reinforce(
            args.id, run_id=args.run or "", new_event=not args.same_event, start=args.project
        )
        _emit(item, args.json,
              [f"{item['id']} 置信度升为 {item['confidence']:.2f}"
               f"（独立事件 {item['distinct_events']} 次）"])
        return 0

    if action == "retire":
        item = memory_mod.retire(args.id, why=args.note or "", start=args.project)
        _emit(item, args.json, [f"{item['id']} 已停用，记录保留"])
        return 0

    if action == "decay":
        result = memory_mod.decay(start=args.project)
        _emit(result, args.json, [f"衰减 {len(result['decayed'])} 条 / 共 {result['total']} 条"])
        return 0

    print(f"未知的 memory 动作：{action}", file=sys.stderr)
    return 2


def cmd_audit(args: argparse.Namespace) -> int:
    target = args.input
    if target is None:
        if not args.run:
            print("audit 需要 --input <运行记录目录> 或 --run <run_id>", file=sys.stderr)
            return 2
        target = run_record.run_dir_for(args.run, args.project)
    result = audit_mod.audit(target, args.memory)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"审计结论：{result['verdict']}  （run: {result.get('run_id')}）"]
    for check in result["checks"]:
        mark = {"pass": "✓", "fail": "✗", "skip": "—"}[check["status"]]
        lines.append(f"  {mark} {check['id']} {check['name']}：{check['detail']}")
    if result["missing_artifacts"]:
        lines.append(f"  缺失产出：{'、'.join(result['missing_artifacts'])}")
    if result.get("error"):
        lines.append(f"  {result['error']}")
    _emit(result, args.json, lines)
    if args.exit_zero:
        return 0
    return 0 if result["verdict"] == "pass" else 1


def cmd_check_verdict(args: argparse.Namespace) -> int:
    problems = audit_mod.verify_verdict_file(args.input, args.check)
    payload = {"check": args.check, "input": args.input, "ok": not problems, "problems": problems}
    _emit(
        payload,
        args.json,
        [f"{'✓' if not problems else '✗'} 结论自检 {args.check}"] + [f"  - {p}" for p in problems],
    )
    return 0 if not problems else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    report: dict[str, Any] = {}
    lines: list[str] = []

    bound = workspace.is_bound(args.project)
    report["bound"] = bound
    report["project_root"] = str(workspace.project_root(args.project))
    lines.append(f"项目根：{report['project_root']}")
    if not bound:
        lines.append("✗ 未绑定品牌工作空间。先跑 bind。")
        _emit(report, args.json, lines)
        return 1

    ws = workspace.load(args.project)
    report["workspace"] = ws
    root = Path(str(ws["kb_root"]))
    kb_ok = root.is_dir()
    report["kb_exists"] = kb_ok
    lines.append(f"{'✓' if kb_ok else '✗'} 品牌：{ws['brand']}｜知识库：{ws['kb_root']}")
    if not kb_ok:
        lines.append("  知识库目录已失效（改名、移动或换了电脑）。重新绑定：bind --force")

    owner = str(ws.get("teacher") or "")
    report["teacher"] = owner
    lines.append(
        f"{'✓' if owner else '⚠'} 文案老师："
        + (owner if owner else "未记名。记忆归属无法区分，换人使用会混合偏好——建议 bind 时补 --teacher")
    )

    hosts = workspace.official_hosts(ws)
    report["official_hosts"] = hosts
    lines.append(
        f"{'✓' if hosts else '⚠'} 官方来源白名单："
        + ("、".join(hosts) if hosts else "空。联网取证会被 official check-url 一律拒绝")
    )

    if kb_ok:
        fresh = index_kb.freshness(args.project)
        report["index"] = fresh
        if not fresh.get("indexed"):
            lines.append("✗ 尚未建立索引。跑 index。")
        else:
            changed = fresh.get("changed") or 0
            lines.append(
                f"{'✓' if changed == 0 else '⚠'} 索引：{fresh['indexed']} 条"
                f"（新增 {fresh.get('new', 0)}，失踪 {fresh.get('missing', 0)}）"
                f"，建于 {fresh.get('built_at')}"
            )
            if changed:
                lines.append("  跑一次 index 做增量更新。")
            if fresh.get("skipped"):
                lines.append(
                    f"  ⚠ 源库里有 {fresh['skipped']} 项被跳过（符号链接或不可读）："
                    + "；".join(fresh.get("sample_skipped") or [])
                )

    try:
        mem = memory_mod.listing(brand=str(ws["brand"]), teacher=owner or None, start=args.project)
        report["memory"] = {
            "count": mem["count"],
            "pending": mem["pending_confirmation"],
            "methodology_candidates": mem["methodology_candidates"],
            "other_teachers": mem.get("other_teachers") or [],
        }
        lines.append(
            f"✓ 记忆：{mem['count']} 条，待确认 {len(mem['pending_confirmation'])} 条，"
            f"方法论候选 {mem['methodology_candidates']} 条"
        )
        if mem.get("other_teachers"):
            lines.append(
                f"  ⚠ 项目里混有其他老师的记忆：{'、'.join(mem['other_teachers'])}。"
                f"一个工作空间只服务一位老师，换人请新建项目目录。"
            )
    except memory_mod.MemoryError_ as exc:
        report["memory_error"] = str(exc)
        lines.append(f"✗ 记忆库有问题：{exc}")

    last = run_record.latest(args.project)
    report["last_run"] = last
    if last is None:
        lines.append("— 还没有任何运行记录。")
    else:
        run_dir = run_record.run_dir_for(last["run_id"], args.project)
        reached = run_record.reached_stage(run_dir)
        closed = bool(last.get("closed_at"))
        report["last_run_reached_stage"] = reached
        lines.append(
            f"{'✓' if closed else '⚠'} 上次运行 {last['run_id']}：模式 {last.get('mode')}，"
            f"记录阶段 {last.get('stage')}（{last.get('stage_name')}），"
            f"按产出判断走到 {reached}（{run_record.STAGE_NAMES.get(reached, reached)}）"
            + ("" if closed else "，尚未归档")
        )
        # 附件登记是"老师给的文件到底被当成本次证据了吗"这个问题的唯一答案。
        # 它只在 meta.json 里，而 doctor 是老师会去看的那一条命令。
        attachments = run_record.attachment_paths(last)
        if attachments:
            lines.append(
                f"  证据边界 {last.get('evidence_boundary')}｜已登记附件 {len(attachments)} 份："
                + "；".join(Path(p).name for p in attachments)
            )
        elif last.get("evidence_boundary") == "attachments":
            lines.append("  ⚠ 声明了以附件为准，但一份附件都没登记——open 时漏了 --attach")
        if reached < (last.get("stage") or 0):
            lines.append("  标记的阶段比实际产出更靠后——某个角色没有落回执。")

    _emit(report, args.json, lines)
    return 0


# --- 参数 -------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="blueink.py",
        description="BlueInk 单命令入口：绑定、索引、检索、运行记录、记忆与审计",
    )
    parser.add_argument("--project", help="项目根目录（默认从当前目录向上找 .blueink/）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出，便于子智能体消费")
    # 同一组全局参数也挂到每个子命令上，这样 `--json audit …` 和 `audit --json` 都能用。
    # 不做这一步的代价是真实的：子智能体被告知"加 --json 就能拿结构化输出"，它自然
    # 会写在子命令后面，然后拿到一句 argparse 的 "unrecognized arguments: --json"——
    # 一个只在某个位置生效的参数，等于一个会随机失败的参数。
    # SUPPRESS 是关键：没传时子解析器不写这个键，于是不会把全局传进来的值清成 False。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--project", default=argparse.SUPPRESS,
                        help="项目根目录（默认从当前目录向上找 .blueink/）")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="以 JSON 输出，便于子智能体消费")
    sub = parser.add_subparsers(dest="command", required=True, parser_class=(
        lambda **kw: argparse.ArgumentParser(parents=[common], **kw)))

    p = sub.add_parser("bind", help="绑定单品牌单老师工作空间")
    p.add_argument("--brand", required=True, help="品牌名，写进稿子要认的那个名字")
    p.add_argument("--kb", required=True, help="该品牌知识库目录")
    p.add_argument("--teacher", required=True,
                   help="负责这个品牌的文案老师；必填，记忆归属靠它隔离")
    p.add_argument("--brand-key", dest="brand_key", help="ASCII 短标识，默认自动推导")
    p.add_argument("--official", action="append", help="官方来源域名或 URL，可重复")
    p.add_argument("--notes", help="这个工作空间的特殊约定")
    p.add_argument("--create", action="store_true",
                   help="知识库目录还不存在时，按标准语料布局建出来再绑定")
    p.add_argument("--force", action="store_true",
                   help="同品牌同老师迁移知识库路径，或确认绕过空目录／集合层启发式；"
                        "不能改品牌或老师")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("status", help="看当前绑定")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("check-brand", help="本次要写的品牌与当前知识库是否匹配")
    p.add_argument("--brand", required=True, help="本次这一稿要写的品牌")
    p.set_defaults(func=cmd_check_brand)

    p = sub.add_parser("index", help="建立或增量更新旁路索引")
    p.add_argument("--full", action="store_true", help="忽略现有索引，全量重建")
    p.add_argument("--limit", type=int, help="只扫前 N 个文件（试跑用）")
    p.set_defaults(func=cmd_index)

    p = sub.add_parser("retrieve", help="按任务检索证据切片")
    p.add_argument("--query", help="关键词，空格分隔")
    p.add_argument("--category", help="品类，如 新闻稿 / 媒体观点供稿")
    p.add_argument("--track", choices=["fact", "style", "strategy"], help="取证轨")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--since", help="只要这个日期之后的证据，如 2026-01-01")
    p.add_argument("--loose", action="store_true", help="品类不命中也保留（降权）")
    p.add_argument("--include-instruction-artifacts", dest="include_instruction_artifacts",
                   action="store_true",
                   help="连知识库里的历史技能提示词一起返回（只在审计这些技能包时用）")
    p.add_argument("--run", help="把这次检索记进该 run_id 的轨迹")
    p.set_defaults(func=cmd_retrieve)

    p = sub.add_parser("official", help="官方来源白名单：查看、追加、访问前校验")
    p.add_argument("action", choices=["list", "add", "check-url"])
    p.add_argument("--url", action="append", help="要校验或追加的地址，可重复")
    p.set_defaults(func=cmd_official)

    p = sub.add_parser("open", help="开启一次运行")
    p.add_argument("--mode", default="生成", choices=list(run_record.MODES))
    p.add_argument("--brand", help="本次这一稿要写的品牌。给了就与绑定品牌核对，"
                                   "不一致直接拒绝并给出出路；不给则按绑定品牌处理")
    p.add_argument("--attach", action="append", metavar="绝对路径",
                   help="老师本次显式提供的附件，可重复。登记路径与内容哈希；"
                        "登记过的附件不受绑定根限制，未登记就读会被审计判为越界。"
                        "未绑定知识库时，附件就是本次唯一的证据来源")
    p.add_argument("--evidence-boundary", choices=list(run_record.EVIDENCE_BOUNDARIES),
                   help="attachments=以附件为准，先只用附件成稿；kb=允许在绑定库内自主检索。"
                        "不给时：有附件默认 attachments，无附件默认 kb")
    p.add_argument("--started-via", choices=list(run_record.ENTRY_ALIASES),
                   default=run_record.ENTRY,
                   help="记录用户实际使用的 Claude Code 显式入口；同名冲突时可能是命名空间形式")
    p.set_defaults(func=cmd_open)

    p = sub.add_parser("stage", help="标记运行走到了哪一步")
    p.add_argument("--run", required=True)
    p.add_argument("--to", type=int, required=True)
    p.set_defaults(func=cmd_stage)

    p = sub.add_parser("close", help="归档一次运行")
    p.add_argument("--run", required=True)
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("purge", help="按留存策略清理旧运行记录（默认试运行）")
    p.add_argument("--keep-days", type=int, default=run_record.KEEP_DAYS,
                   help=f"保留多少天内的运行（默认 {run_record.KEEP_DAYS}）")
    p.add_argument("--keep-runs", type=int, default=run_record.KEEP_RUNS,
                   help=f"无论多旧，至少保留最近几次（默认 {run_record.KEEP_RUNS}）")
    p.add_argument("--apply", action="store_true", help="真的删除；不加只打印将删除什么")
    p.set_defaults(func=cmd_purge)

    p = sub.add_parser("memory", help="条件化记忆的读写")
    p.add_argument(
        "action",
        choices=["list", "add", "confirm", "counter", "cancel", "reinforce", "retire", "decay"],
    )
    p.add_argument("--id", help="记忆 id")
    p.add_argument("--file", help="反馈归因员回执 feedback.json")
    p.add_argument("--note", help="一句话说明")
    p.add_argument("--narrow", help="给旧结论补一条不适用范围")
    p.add_argument("--scope", choices=list(memory_mod.VALID_SCOPES))
    p.add_argument("--brand")
    p.add_argument("--teacher", help="记忆归属的文案老师，默认取工作空间登记的那位")
    p.add_argument("--run")
    p.add_argument("--min-confidence", dest="min_confidence", type=float)
    p.add_argument("--include-retired", dest="include_retired", action="store_true")
    p.add_argument("--same-event", dest="same_event", action="store_true",
                   help="同一传播事件内的同向证据，只 +0.05")
    p.set_defaults(func=cmd_memory)

    p = sub.add_parser("audit", help="六项验收契约的机械审计")
    p.add_argument("--input", help="运行记录目录")
    p.add_argument("--run", help="run_id，等价于 --input <runs>/<run_id>")
    p.add_argument("--output", help="把结论 JSON 写到这里")
    p.add_argument("--memory", help="记忆库快照路径（默认自动查找）")
    p.add_argument("--exit-zero", dest="exit_zero", action="store_true",
                   help="只要成功产出结论就退出 0（评测用；人工排查时不要加）")
    p.set_defaults(func=cmd_audit)

    p = sub.add_parser("check-verdict", help="自检一份审计结论是否可用于定位")
    p.add_argument("input", help="审计结论 JSON")
    p.add_argument("--check", required=True, choices=list(audit_mod.VERDICT_CHECKS))
    p.set_defaults(func=cmd_check_verdict)

    p = sub.add_parser("doctor", help="一条命令看清当前状态")
    p.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (workspace.WorkspaceError, memory_mod.MemoryError_,
            official_mod.OfficialSourceError) as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
