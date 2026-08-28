# blueink-suite · Claude Code / Windows 直装脚本
#
# 推荐优先使用 README 里的 Claude Code marketplace 安装。本脚本把完整插件放到
# $HOME\.claude\skills\blueink-suite；默认生成读取 policies\common-policy.yaml
# 与 references\generate.md。

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
$SkillName = 'blueink-suite'
$Src = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetRoot = Join-Path $HOME '.claude\skills'
$Target = Join-Path $TargetRoot $SkillName

foreach ($required in @('SKILL.md', '.claude-plugin\plugin.json', 'policies\common-policy.yaml', 'references\generate.md', 'references\research.md', 'references\feedback.md', 'references\troubleshooting.md', 'scripts')) {
    if (-not (Test-Path (Join-Path $Src $required))) {
        throw "源目录不完整，缺 $required：$Src"
    }
}

$PythonExe = $null
$PythonArgs = @()
if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonExe = 'py'
    $PythonArgs = @('-3')
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonExe = 'python'
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $PythonExe = 'python3'
} else {
    throw '找不到 Python。BlueInk 需要 Python 3.9 或更高。'
}

& $PythonExe @PythonArgs -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'
if ($LASTEXITCODE -ne 0) { throw '当前 Python 低于 3.9。' }

Write-Host "源目录：$Src"
Write-Host "目标：  $Target"

if ((Test-Path $Target) -and -not $Force) {
    if ($DryRun) {
        Write-Host '目标已存在；正式安装需加 -Force 或在交互提示中确认。'
    } else {
        $answer = Read-Host '目标已存在，将替换现有版本。继续？[y/N]'
        if ($answer -notmatch '^(y|Y|yes|YES)$') { Write-Host '已取消。'; exit 0 }
    }
}

if ($DryRun) {
    Write-Host "[dry-run] 将把完整 Claude Code 插件复制到 $Target"
    exit 0
}

New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
$Stage = Join-Path $TargetRoot ('.blueink-install-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Force -Path $Stage | Out-Null
    $StagedPlugin = Join-Path $Stage $SkillName
    Copy-Item -Recurse -Path $Src -Destination $StagedPlugin
    foreach ($junk in @('.git', '.gitignore', '.DS_Store', 'scripts\__pycache__', 'evals', 'commands', 'DECISIONS.md', 'DESIGN_NOTES.md', 'EVOLUTION.md')) {
        $path = Join-Path $StagedPlugin $junk
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
    if (Test-Path $Target) { Remove-Item -Recurse -Force $Target }
    Move-Item -Path $StagedPlugin -Destination $Target
} finally {
    if (Test-Path $Stage) { Remove-Item -Recurse -Force $Stage }
}

Write-Host "✓ 已安装 Claude Code 插件：$Target"
Write-Host '  单智能体运行；默认读取通用规范与 references\generate.md。'
Write-Host '  新开 Claude Code 会话后输入：/blueink-suite <你的需求>'
