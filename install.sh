#!/bin/sh
# blueink-suite · Claude Code / macOS 直装脚本
#
# 推荐优先使用 README 里的 Claude Code marketplace 安装。这个脚本只作为本地源码
# 直装入口：把完整插件放到 ~/.claude/skills/blueink-suite。运行时只有一个 Skill，
# 六份阶段指导从 references/stages/ 按需读取，不注册全局或插件子智能体。

set -eu

SKILL_NAME="blueink-suite"
SRC="$(cd "$(dirname "$0")" && pwd)"
TARGET_ROOT="${HOME}/.claude/skills"
TARGET="${TARGET_ROOT}/${SKILL_NAME}"
FORCE=0
DRY_RUN=0

while [ $# -gt 0 ]; do
    case "$1" in
        --force) FORCE=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help)
            echo "用法：./install.sh [--force] [--dry-run]"
            exit 0
            ;;
        *) echo "未知参数：$1（只支持 --force / --dry-run）" >&2; exit 2 ;;
    esac
    shift
done

if [ "$(uname -s)" != "Darwin" ]; then
    echo "install.sh 只支持 macOS；Windows 请运行 .\\install.ps1。" >&2
    exit 2
fi

for required in SKILL.md .claude-plugin/plugin.json references references/stages scripts; do
    if [ ! -e "${SRC}/${required}" ]; then
        echo "源目录不完整，缺 ${required}：${SRC}" >&2
        exit 1
    fi
done

if ! command -v python3 >/dev/null 2>&1; then
    echo "找不到 python3。BlueInk 需要 Python 3.9 或更高。" >&2
    exit 2
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "当前 python3 低于 3.9。" >&2
    exit 2
fi

echo "源目录：${SRC}"
echo "目标：  ${TARGET}"

if [ -e "${TARGET}" ] && [ "${FORCE}" -ne 1 ]; then
    if [ "${DRY_RUN}" -eq 1 ]; then
        echo "目标已存在；正式安装需加 --force 或在交互提示中确认。"
    elif [ -t 0 ]; then
        printf "目标已存在，将替换现有版本。继续？[y/N] "
        read -r answer || answer=""
        case "${answer}" in y|Y|yes|YES) ;; *) echo "已取消。"; exit 0 ;; esac
    else
        echo "目标已存在；非交互环境请加 --force。" >&2
        exit 2
    fi
fi

if [ "${DRY_RUN}" -eq 1 ]; then
    echo "[dry-run] 将把完整 Claude Code 插件复制到 ${TARGET}"
    exit 0
fi

mkdir -p "${TARGET_ROOT}"
STAGE="$(mktemp -d "${TARGET_ROOT}/.blueink-install.XXXXXX")"
trap 'rm -rf "${STAGE}"' EXIT HUP INT TERM
cp -R "${SRC}" "${STAGE}/${SKILL_NAME}"
rm -rf "${STAGE}/${SKILL_NAME}/.git" \
       "${STAGE}/${SKILL_NAME}/scripts/__pycache__"
rm -f "${STAGE}/${SKILL_NAME}/.gitignore"

if [ -e "${TARGET}" ]; then
    rm -rf "${TARGET}"
fi
mv "${STAGE}/${SKILL_NAME}" "${TARGET}"

echo "✓ 已安装 Claude Code 插件：${TARGET}"
echo "  单智能体运行；六份阶段指导位于 references/stages/。"
echo "  新开 Claude Code 会话后输入：/blueink-suite <你的需求>"
