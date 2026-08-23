#!/bin/sh
# blueink-suite 安装脚本
#
# 做两件事：把技能目录复制到目标工具的技能路径；把六个角色契约注册为 Claude Code
# 原生子智能体。
#
# 注册子智能体是**默认行为**，不是可选项。角色契约里的工具白名单只有在原生注册时
# 才被工具层强制执行——写作者"没有检索工具"这句话，靠 general-purpose 是实现不了
# 的，只能靠事后审计发现。用 --no-agents 可以跳过，此时会打印一行警告说明代价。
#
# 用法：
#   ./install.sh                    安装到 Claude Code 并注册六个子智能体
#   ./install.sh --no-agents        只装技能，不注册子智能体（工具边界退化为自律）
#   ./install.sh --codex            安装到 Codex CLI（~/.codex/skills/，不支持子智能体）
#   ./install.sh --to <目录>        安装到指定的技能目录
#   ./install.sh --force            已存在时直接覆盖，不询问（非交互环境必需）
#   ./install.sh --dry-run          只打印将要执行的动作
#
# 退出码：0 成功 ／ 1 源目录不完整 ／ 2 参数或目标路径有问题
#
# 实现注记：所有变量都写成 ${VAR} 形式。写成 $VAR 时，紧跟其后的中文标点会被
# 某些 bash 版本当成变量名的一部分，触发 "unbound variable"。

set -eu

SKILL_NAME="blueink-suite"
SRC="$(cd "$(dirname "$0")" && pwd)"

TARGET_ROOT="${HOME}/.claude/skills"
AGENTS_ROOT="${HOME}/.claude/agents"
WITH_AGENTS=1
DRY_RUN=0
FORCE=0

while [ $# -gt 0 ]; do
    case "$1" in
        --no-agents)   WITH_AGENTS=0 ;;
        --with-agents) WITH_AGENTS=1 ;;   # 保留兼容，已是默认
        --codex)       TARGET_ROOT="${HOME}/.codex/skills"; AGENTS_ROOT="" ;;
        --to)          shift
                       if [ $# -eq 0 ]; then
                           echo "--to 需要一个目录参数" >&2
                           exit 2
                       fi
                       TARGET_ROOT="$1"; AGENTS_ROOT="" ;;
        --force)       FORCE=1 ;;
        --dry-run)     DRY_RUN=1 ;;
        -h|--help)     sed -n '10,19p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *)             echo "未知参数：$1 （试试 --help）" >&2; exit 2 ;;
    esac
    shift
done

# --- 源目录完整性 ---
for required in SKILL.md AGENTS.md requirements.txt agents commands references scripts; do
    if [ ! -e "${SRC}/${required}" ]; then
        echo "源目录不完整，缺 ${required} ：${SRC}" >&2
        exit 1
    fi
done

TARGET="${TARGET_ROOT}/${SKILL_NAME}"

run() {
    if [ "${DRY_RUN}" -eq 1 ]; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

echo "源目录：${SRC}"
echo "目标：  ${TARGET}"

if [ -e "${TARGET}" ]; then
    if [ "${FORCE}" -eq 1 ] || [ "${DRY_RUN}" -eq 1 ]; then
        echo "目标已存在，将被覆盖。"
    elif [ -t 0 ]; then
        printf "目标已存在，将被覆盖。继续？[y/N] "
        read -r answer || answer=""
        case "${answer}" in
            y|Y|yes|YES) ;;
            *) echo "已取消。"; exit 0 ;;
        esac
    else
        echo "目标已存在，且当前不是交互终端。加 --force 明确同意覆盖。" >&2
        exit 2
    fi
    run rm -rf "${TARGET}"
fi

run mkdir -p "${TARGET_ROOT}"
if [ "${DRY_RUN}" -eq 0 ] && [ ! -w "${TARGET_ROOT}" ]; then
    echo "目标路径不可写：${TARGET_ROOT}" >&2
    exit 2
fi

run cp -R "${SRC}" "${TARGET}"
# 仓库元数据与字节码不属于技能内容
run rm -rf "${TARGET}/.git" "${TARGET}/.gitignore" "${TARGET}/scripts/__pycache__"
if [ "${DRY_RUN}" -eq 1 ]; then
    echo "（dry-run，以上动作均未执行）"
else
    echo "✓ 技能已安装：${TARGET}"
fi

if [ "${WITH_AGENTS}" -eq 1 ]; then
    if [ -z "${AGENTS_ROOT}" ]; then
        echo "⚠ 当前目标不支持原生子智能体注册，已跳过。"
        echo "  角色边界将退化为提示词自律，只能靠 audit 事后发现越权。"
    else
        run mkdir -p "${AGENTS_ROOT}"
        # 先清掉本技能注册过的旧角色，再装当前这一组。技能目录已经这样处理了
        # （第 89 行 rm -rf），Agent 目录不这样做会留下一个真实的窗口：角色被改名
        # 或删除后，旧的 blueink-<old>.md 仍然注册在册、仍然可被调用，执行的是一份
        # 已经从技能里删掉的契约——这正是"外部提示词接管本次判断"那一类失败。
        # 只删 blueink- 前缀的，不碰用户自己的其他 Agent。
        for stale in "${AGENTS_ROOT}"/blueink-*.md; do
            [ -e "${stale}" ] || continue
            run rm -f "${stale}"
        done
        for role in "${SRC}"/agents/*.md; do
            [ -e "${role}" ] || continue
            name="$(basename "${role}")"
            run cp "${role}" "${AGENTS_ROOT}/blueink-${name}"
        done
        if [ "${DRY_RUN}" -eq 0 ]; then
            echo "✓ 六个角色已注册到 ${AGENTS_ROOT} （文件名前缀 blueink-）"
            echo "  写作者、策略师、核验员、反方、归因员由此真的拿不到检索工具。"
        fi
    fi
else
    echo "⚠ 按 --no-agents 跳过子智能体注册。"
    echo "  代价：写作者仍能重新扫库、核验员仍能搜索新来源，边界只靠自律，"
    echo "  越权要等 audit 事后才能发现。生产使用不建议。"
fi

echo ""
echo "下一步：直接在你要写稿的项目目录下启动 /blueink-suite <你的需求>。"
echo "还没绑定知识库时，它会在访谈里一次问一件地问出品牌、你的名字和知识库目录，"
echo "然后替你写好配置——不需要你先敲命令。"
echo ""
echo "想手工完成绑定的话（在你要写稿的项目目录里执行一次）："
echo "  python3 ${TARGET}/scripts/blueink.py bind \\"
echo "      --brand \"理想汽车\" --teacher \"<你的名字>\" \\"
echo "      --kb \"<该品牌知识库目录>\" --official \"<官网域名>\""
echo "  python3 ${TARGET}/scripts/blueink.py index"
echo "  python3 ${TARGET}/scripts/blueink.py doctor"
echo ""
echo "这个品牌还没有知识库目录时，给 bind 加 --create：它会按标准语料布局建出来，"
echo "之后把稿件丢进去跑一次 index 就能用，不需要先分类或改文件名。"
echo ""
echo "只想参考几份指定文件、本次不用知识库："
echo "  python3 ${TARGET}/scripts/blueink.py open --mode 生成 \\"
echo "      --brand \"<品牌>\" --attach \"<文件绝对路径>\""
echo ""
echo "绑定信息、索引与记忆写在项目内的 .blueink/ 下，不在技能目录里。"
