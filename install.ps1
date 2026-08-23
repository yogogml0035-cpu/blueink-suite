# blueink-suite 安装脚本（Windows / PowerShell）
#
# 与 install.sh 等价。注册六个原生子智能体是**默认行为**：角色契约里的工具白名单
# 只有在原生注册时才被工具层强制执行——写作者"没有检索工具"这句话靠
# general-purpose 是实现不了的，只能靠事后审计发现。
#
# 用法：
#   .\install.ps1                   安装到 Claude Code 并注册六个子智能体
#   .\install.ps1 -NoAgents         只装技能（工具边界退化为自律，生产不建议）
#   .\install.ps1 -To <目录>        安装到指定的技能目录
#   .\install.ps1 -Force            已存在时直接覆盖
#   .\install.ps1 -DryRun           只打印将要执行的动作

[CmdletBinding()]
param(
    [switch]$NoAgents,
    [string]$To,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$SkillName  = 'blueink-suite'
$Src        = Split-Path -Parent $MyInvocation.MyCommand.Path
$TargetRoot = if ($To) { $To } else { Join-Path $HOME '.claude\skills' }
$AgentsRoot = if ($To) { $null } else { Join-Path $HOME '.claude\agents' }
$Target     = Join-Path $TargetRoot $SkillName

foreach ($required in @('SKILL.md', 'AGENTS.md', 'requirements.txt', 'agents', 'commands', 'references', 'scripts')) {
    if (-not (Test-Path (Join-Path $Src $required))) {
        Write-Error "源目录不完整，缺 $required ：$Src"
        exit 1
    }
}

Write-Host "源目录：$Src"
Write-Host "目标：  $Target"

if (Test-Path $Target) {
    if (-not ($Force -or $DryRun)) {
        $answer = Read-Host '目标已存在，将被覆盖。继续？[y/N]'
        if ($answer -notmatch '^(y|Y|yes|YES)$') { Write-Host '已取消。'; exit 0 }
    }
    if (-not $DryRun) { Remove-Item -Recurse -Force $Target }
    else { Write-Host "  [dry-run] Remove-Item -Recurse -Force $Target" }
}

if ($DryRun) {
    Write-Host "  [dry-run] Copy-Item -Recurse $Src $Target"
} else {
    New-Item -ItemType Directory -Force -Path $TargetRoot | Out-Null
    Copy-Item -Recurse -Path $Src -Destination $Target
    foreach ($junk in @('.git', '.gitignore', 'scripts\__pycache__')) {
        $path = Join-Path $Target $junk
        if (Test-Path $path) { Remove-Item -Recurse -Force $path }
    }
    Write-Host "✓ 技能已安装：$Target"
}

if ($NoAgents) {
    Write-Host '⚠ 按 -NoAgents 跳过子智能体注册。'
    Write-Host '  代价：写作者仍能重新扫库、核验员仍能搜索新来源，边界只靠自律，'
    Write-Host '  越权要等 audit 事后才能发现。生产使用不建议。'
} elseif (-not $AgentsRoot) {
    Write-Host '⚠ 指定了 -To，当前目标不支持原生子智能体注册，已跳过。'
} else {
    if ($DryRun) {
        Write-Host "  [dry-run] 复制 agents\*.md 到 $AgentsRoot（前缀 blueink-）"
    } else {
        New-Item -ItemType Directory -Force -Path $AgentsRoot | Out-Null
        # 先清掉本技能注册过的旧角色（只清 blueink- 前缀，不碰用户自己的 Agent）。
        # 不清的话，角色被改名或删除后旧文件仍注册在册、仍可调用。
        Get-ChildItem -Path $AgentsRoot -Filter 'blueink-*.md' -ErrorAction SilentlyContinue |
            Remove-Item -Force
        Get-ChildItem -Path (Join-Path $Src 'agents') -Filter '*.md' | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $AgentsRoot ("blueink-" + $_.Name)) -Force
        }
        Write-Host "✓ 六个角色已注册到 $AgentsRoot （文件名前缀 blueink-）"
        Write-Host '  写作者、策略师、核验员、反方、归因员由此真的拿不到检索工具。'
    }
}

Write-Host ''
Write-Host '下一步（在你要写稿的项目目录里执行一次）：'
Write-Host "  python3 `"$Target\scripts\blueink.py`" bind ``"
Write-Host '      --brand "理想汽车" --teacher "<你的名字>" ``'
Write-Host '      --kb "<该品牌知识库目录>" --official "<官网域名>"'
Write-Host "  python3 `"$Target\scripts\blueink.py`" index"
Write-Host "  python3 `"$Target\scripts\blueink.py`" doctor"
Write-Host ''
Write-Host '然后在该目录下用显式命令启动：/blueink-suite <你的需求>'
Write-Host '绑定信息、索引与记忆写在项目内的 .blueink\ 下，不在技能目录里。'
