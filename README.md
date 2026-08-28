# blueink-suite

BlueInk 是一个面向汽车公关文案的证据驱动编辑决策 Skill。

> [!IMPORTANT]
> 当前只支持 **Claude Code**。不要尝试安装到 Codex、ChatGPT、Cursor 或其他 Agent 平台。

## 给安装 Agent 的任务

请把本仓库作为完整的 Claude Code 插件安装到当前用户环境。默认使用 `user` scope，使它能在该用户的不同项目中使用。

安装时必须保留运行插件，不能只复制 `SKILL.md`。默认生成读取 `policies/common-policy.yaml` 和唯一运行指导 `references/generate.md`；`research.md`、`feedback.md` 和 `troubleshooting.md` 只在对应条件出现时加载。项目首次使用会先绑定一个不包含其他品牌的历史稿件共同根目录；明确附件仍是事实边界，历史终稿只走风格参考轨。正文通过交付前通用规范检查后，才作为唯一可修改稿交付并继续受限核验；`scripts/` 保存工作空间、索引、正文与三份侧车、阶段耗时和审计能力。源码仓库里的评测夹具与设计文档不属于直装运行包。

### 1. 检查环境

确认本机具备：

- Claude Code，并且 `claude plugin` 命令可用；
- 需要 Python 3.9 或更高；
- Git 和访问 GitHub 的网络。

macOS：

```bash
claude --version
claude plugin --help
python3 -c 'import sys; assert sys.version_info >= (3, 9), sys.version'
```

Windows PowerShell：

```powershell
claude --version
claude plugin --help
py -3 -c "import sys; assert sys.version_info >= (3, 9), sys.version"
```

如果 Windows 没有 `py`，再尝试 `python`。缺少 Claude Code 或 Python 3.9+ 时停止安装，准确报告缺少的依赖，不要改装到其他平台。

### 2. 检查是否已经安装

```bash
claude plugin marketplace list --json
claude plugin list --json
```

目标标识：

- Marketplace：`blueink-suite`
- Plugin：`blueink-suite@blueink-suite`
- GitHub：`https://github.com/yogogml0035-cpu/blueink-suite`

### 3. 安装或更新

如果 marketplace 尚不存在：

```bash
claude plugin marketplace add https://github.com/yogogml0035-cpu/blueink-suite --scope user
```

如果 marketplace 已存在，先更新索引：

```bash
claude plugin marketplace update blueink-suite
```

如果插件尚未安装：

```bash
claude plugin install blueink-suite@blueink-suite --scope user --yes
```

如果插件已经安装，执行更新，不要重复安装：

```bash
claude plugin update blueink-suite@blueink-suite --scope user --yes
```

`--yes` 只用于完成用户已经明确授权的非交互安装；如果安装过程中出现新的权限、脚本或来源变更，先向用户说明，再继续。

### 4. 验收

再次执行：

```bash
claude plugin list --json
claude plugin details blueink-suite@blueink-suite
```

安装成功必须同时满足：

- `blueink-suite@blueink-suite` 已安装、已启用，scope 为 `user`；
- Claude Code 能识别 1 个 `blueink-suite` Skill；
- 插件目录不存在根级 `agents/`，运行时只使用当前 `/blueink-suite` 智能体；
- `policies/common-policy.yaml` 与 `references/generate.md` 完整可读，明确全品牌硬规则、成稿前方向确认、交付前规范检查、`handoff` 唯一正文交付和 `delivery.md / run.json / verify.json / delivery-check.md` 四份默认产物；
- `references/research.md`、`feedback.md`、`troubleshooting.md` 完整可读且只按条件加载；
- 新开 Claude Code 会话后，显式入口 `/blueink-suite` 可用。

如果当前会话没有刷新插件，先执行 `/reload-plugins`；仍不可用时新开 Claude Code 会话。若其他插件占用了同名裸命令，使用规范化入口：

```text
/blueink-suite:blueink-suite
```

如果新会话直接显示 `Unknown command`，先区分“被禁用”和“未安装”，不要直接重装：

```bash
claude plugin list --json
claude plugin enable blueink-suite@blueink-suite --scope user
```

列表里仍有插件但 `enabled: false` 时，第二条命令恢复启用，然后新开会话；列表里根本没有插件时才回到第 3 步安装。插件被禁用时 `claude plugin details` 也可能返回 `not found`，所以状态判断以 `plugin list --json` 为先。

验收后向用户报告 Claude Code 版本、Python 版本、安装 scope、插件版本和可用入口。不要把“命令执行无报错”单独当成安装成功。

## Marketplace 不可用时的备用安装

只有 marketplace 安装确实失败时，才使用仓库自带的源码安装脚本。先克隆仓库并进入根目录：

```bash
git clone https://github.com/yogogml0035-cpu/blueink-suite.git
cd blueink-suite
```

macOS：

```bash
./install.sh --dry-run
./install.sh
```

Windows PowerShell：

```powershell
.\install.ps1 -DryRun
.\install.ps1
```

脚本会把完整插件安装到当前用户的 `~/.claude/skills/blueink-suite`。如果目标已存在，不要未经确认使用 `--force` 或 `-Force` 覆盖；先判断它是已安装版本、本地修改版，还是一次未完成的安装。

源码直装完成后，同样必须新开 Claude Code 会话并验证 `/blueink-suite`。不要同时保留 marketplace 版与源码直装版，以免出现重复入口或版本混淆。

## 安装后使用

在要写稿的项目目录中启动 Claude Code，然后显式调用：

```text
/blueink-suite <文案需求>
```

第一次在业务项目中使用时，Skill 会先询问该品牌所有过往稿件所在的共同根目录；只有老师明确说“本次只用附件”才一次性绕过绑定。路径记录在业务项目的 `.blueink/workspace.yaml`，源目录保持只读。Skill 仍会在成稿前要求老师确认本次写法。安装 Agent 不需要在安装阶段创建知识库、绑定品牌或执行真实写稿任务。

## 许可

内部使用（proprietary）。新蓝标数字 · 汽车事业群 · AI 内容中台。
