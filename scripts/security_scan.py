#!/usr/bin/env python3
"""安全扫描：这个技能包会不会做出超出"生成文案"的动作。

它扫的是三类真实风险，不是通用漏洞清单：

1. **写源库。** 除老师显式要求的 ``bind --create`` 空骨架初始化外，绑定后的
   索引与写作流程只读源库。脚本里出现对 ``kb_root`` 的写操作就是越界。
2. **越界执行与外发。** 文案生成不需要 ``eval``、``os.system``、``subprocess``
   拼字符串、也不需要往外发数据。出现即报。
3. **凭据与本机路径。** 技能要能整目录复制给另一位老师，所以不能带任何密钥、
   token 或写死的本机路径。

刻意不做的事：不给每个功能点加一层安全检查。这里只看"能不能造成技能承诺之外的
副作用"，其余边界由各自的唯一执行点负责（路径归属在 ``workspace.within``，
官方来源在 ``official.check_url``）。

用法：``python3 scripts/security_scan.py [技能目录]``；退出码 0 干净、1 有发现。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# (正则, 说明, 豁免文件及豁免理由)
#
# 说明文字里刻意不写 `函数名()` 这种形式——带括号的字面量会让扫描器（包括官方那个）
# 把这张规则表本身当成调用点，而一个自己报自己的扫描器很快就没人看了。
RULES: list[tuple[str, str, dict[str, str]]] = [
    (r"\beval\s*\(", "eval 执行任意代码", {}),
    (r"\bexec\s*\(", "exec 执行任意代码", {}),
    (r"os\.system\s*\(", "os.system 执行 shell", {}),
    (r"os\.popen\s*\(", "os.popen 执行 shell", {}),
    (r"subprocess\.[a-z_]+\([^)]*shell\s*=\s*True", "subprocess 用 shell=True 拼命令",
     {"scripts/run_evals.py": "评测规格里的 cmd 本身就是 shell 命令串，由维护者写死在 eval.md 里，"
                              "不接受外部输入；命令型评测的语义要求原样执行"}),
    (r"\b__import__\s*\(", "动态导入", {}),
    (r"pickle\.loads?\s*\(", "pickle 反序列化不可信数据", {}),
    (r"\bshutil\.(rmtree|move)\s*\(", "递归删除或移动文件",
     {"scripts/test_state.py": "只清理自己用 tempfile.mkdtemp 创建的临时目录；"
                               "测试不清理会在每次运行后留下一堆临时夹具",
      "scripts/run_record.py": "purge 删除的是项目内 .blueink/runs/<run_id>/ 这一层派生物，"
                               "范围由 workspace.runs_dir 限定、永不触及 kb_root；"
                               "默认试运行，只有显式 --apply 才真的删。"
                               "留存期本身是隐私要求：运行目录里有访谈原文与交付正文，"
                               "不给删除入口等于承诺永久留存客户内容",
      "scripts/self_check.py": "变异门在自己用 tempfile.mkdtemp 创建的技能副本里施加变异，"
                               "跑完就删；变异必须发生在副本上，绝不能改真实技能目录",
      "scripts/workspace.py": "当前品牌迁移知识库路径时，只删除项目内 .blueink/index/；"
                              "该目录完全可重建，目标由固定工作空间路径构造，不接受外部删除目标；"
                              "learning/、runs/ 与 kb_root 均不触及"}),
    (r"\brequests\.|urllib\.request\.urlopen|\burlopen\s*\(|http\.client\.", "脚本层直接联网",
     {"scripts/run_evals.py": "仅显式 --judge 且存在 ANTHROPIC_API_KEY 时调用"
                              " https://api.anthropic.com/v1/messages；本技能的当前评测规格"
                              "没有 llm-judge 判据，普通运行与全部发布门不会走这条路径"}),
    (r"(?i)(api[_-]?key|secret|token|password|passwd)\s*=\s*[\"'][^\"']{8,}",
     "疑似写死的凭据", {}),
    (r"[\"']/Users/[^\"']+[\"']", "写死的本机绝对路径", {}),
    (r"[\"']~/\.(ssh|aws|config/gcloud)", "指向本机凭据目录", {}),
]

# 技能目录里不该出现品牌语料**文件**。判定按路径而不是按内容：正文里讨论
# 「`【终稿】xxx.md` 即使在初终稿对比目录下也是终稿」是必要的命名约定说明，
# 不是内嵌语料。真正的失败形态是有人把稿件拷进技能包。
CORPUS_PATH_MARKERS = ("【终稿】", "【初稿】", "初终稿对比", "成品参考", "原文资产", "经验总结原稿")

# 对 kb_root 的写操作：源库只读是这个技能最硬的一条承诺
KB_WRITE = re.compile(
    r"(kb_root|source_root|knowledge_root)[^\n]{0,80}"
    r"\.(write_text|write_bytes|open\(\s*[\"'][wax]|mkdir|unlink|rename|replace|touch|rmdir)"
)


def scan(root: Path) -> list[str]:
    """返回发现列表；空列表表示干净。"""
    findings: list[str] = []
    for path in sorted((root / "scripts").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        # 逐行扫，行号是给人看的定位信息——报"某处有问题"等于没报
        for number, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue          # 注释里举反例是允许的
            if stripped.startswith('(r"'):
                continue          # 本扫描器自己的规则表，不是调用点
            for pattern, why, exemptions in RULES:
                if rel in exemptions:
                    continue
                if re.search(pattern, line):
                    findings.append(f"{rel}:{number} {why} → {stripped[:90]}")
        for match in KB_WRITE.finditer(text):
            number = text[: match.start()].count("\n") + 1
            findings.append(f"{rel}:{number} 对源知识库的写操作 → {match.group(0)[:90]}")

    for path in sorted(root.rglob("*")):
        if ".git" in path.parts:
            continue
        if path.is_symlink():
            findings.append(f"{path.relative_to(root)} 是符号链接——技能包里不该有")
            continue
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if "evals/" in rel:
            continue              # 夹具里出现语料形态的路径字符串是刻意的
        for marker in CORPUS_PATH_MARKERS:
            if marker in rel:
                findings.append(
                    f"{rel} 看起来是品牌语料文件——技能本体必须不含任何品牌内容，"
                    f"否则换品牌就要改动技能本身"
                )
                break

    return findings


def exemptions(root: Path) -> list[str]:
    """已豁免项及理由。豁免必须可见——静默豁免等于没扫。"""
    out: list[str] = []
    for _pattern, why, items in RULES:
        for rel, reason in items.items():
            if (root / rel).is_file():
                out.append(f"{rel}：{why} —— 豁免理由：{reason}")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="blueink-suite 安全扫描")
    parser.add_argument("skill_dir", nargs="?", default=str(Path(__file__).resolve().parent.parent))
    args = parser.parse_args()
    root = Path(args.skill_dir).resolve()
    print(f"扫描目录：{root}")
    findings = scan(root)
    for note in exemptions(root):
        print(f"  [豁免] {note}")
    if findings:
        print(f"状态：FINDINGS（{len(findings)} 项）")
        for item in findings:
            print(f"  [发现] {item}")
        return 1
    print("状态：CLEAN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
