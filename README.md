# whose-ball · 球在谁手里

> **If you are an AI agent** and the user asked you to set this up: read [AGENTS.md](AGENTS.md) and follow it. Everything below is for humans.
>
> **给 AI agent：** 用户让你装这个工具时，直接按 [AGENTS.md](AGENTS.md) 操作。

一个零依赖（Python 3.8+ 标准库）的任务跟踪器，专门回答一个问题：**每件进行中的事，现在球在谁手里、等了几天？**

它和普通 todo 的区别：核心对象不是"任务"而是"等待项"。一个项目任何时刻都处在"等客户 / 等供应商 / 等内部同事 / 轮到我"四种状态之一，超过 N 天没动静的会亮警告。配上时间线、笔记、链接，就是一个人跟几十个并行事项时需要的全部。

它真正的用法是**交给 AI agent 维护**。你在 Claude Code 里说一句"客户回了，同意报价"，agent 就该在同一回合把时间线记上、把等待项从"等客户"转成"轮到我"。本 repo 附带一套 Claude Code hooks 把这个行为钉死（见下文"Hooks"）。

## 快速开始（人）

```bash
git clone https://github.com/gitlqs/whose-ball.git
cd whose-ball
python tracker.py add 示例项目 -t "Acme · 要一台 X-200" -c 主线 -s 待澄清
python tracker.py wait 示例项目 customer "回复报价" --who Bob
python tracker.py list
python tracker.py serve        # http://127.0.0.1:8765 看板
```

Windows 双击 `tracker.bat`：自动起 server 并用 Edge/Chrome 的独立窗口打开看板。

## 装进一个 Claude Code 项目

在 Claude Code 里把本 repo 链接发给 agent，说：

> 我要用这个工具，帮我 setup 好。

agent 会按 [AGENTS.md](AGENTS.md) 跑 `install.py`，把 tracker、hooks、CLAUDE.md 段落、preview 配置一次装好。手动的话：

```bash
python /path/to/whose-ball/install.py --target /path/to/your/project [--subdir tracker] [--stages "a,b,c"] [--no-strict]
```

装完重开会话，hooks 才生效。

## 数据模型

| 表 | 含义 |
|---|---|
| projects | 项目：代号（唯一，支持子串匹配）、一句话身份、类别、阶段、active/closed |
| waits | 等待项：party（customer/vendor/internal/me）、等谁、等什么、起始日、最后催促日、是否完成 |
| events | 时间线，所有操作自动落一条，`log` 手动加 |
| notes | 笔记，同名标题覆盖（适合"档案""需求"这种长期维护的） |
| refs | 链接/路径，带标签 |
| settings | `stale_days`（默认 5）、`pipeline_stages`（看板顶部流水线） |

全部存在 `tracker.db`（SQLite），和 `tracker.py` 同目录。web 写操作前自动快照到 `tracker.db.undo`，看板上可撤回一步。

## CLI

```
add 代号 -t "一句话" -c 类别 -s 阶段        新项目
wait 代号 customer|vendor|internal|me "等什么" --who 人名 [--since 日期] [--clear]
                                              球转手；--clear 先了结旧等待项
done WAIT_ID ["备注"]                          等待项完成
nudge WAIT_ID ["备注"]                         记一次催促（重置计时）
log 代号 "发生了什么" [--ts 时间]              时间线
note 代号 "标题" "内容" | -f 文件               笔记
ref 代号 "标签" "URL或路径"                     链接
update 代号 [-t] [-c] [-s 阶段] [--status]     改字段
close 代号 ["原因"]                            关闭，自动了结所有等待项
list [--all]                                  按等待方分组，超期标 ⚠
show 代号 [-n 15]                              详情 + 最近时间线
set key value                                 stale_days / pipeline_stages
serve [--port 8765]                           web 看板
```

## Hooks：让 agent 每回合都同步

`install.py` 会往 `.claude/settings.json` 合并三个 hook，都是 `.claude/hooks/tracker_hook.py` 的子命令：

| Hook | 作用 |
|---|---|
| SessionStart | 把 `list` 看板注入上下文，agent 开场就知道现状 |
| UserPromptSubmit | 每条消息附一句"有进展就本回合写 tracker，别先问"；同时按长度/关键词标记这条消息像不像进展 |
| Stop（严格模式） | 消息像进展、但本回合 `tracker.db` 没被写过 → 拦一次让 agent 自查补写。没变化就直接结束，不会死循环 |

配置在 `.claude/tracker_hooks.json`（`strict`、`min_chars`、`keywords`、`tracker_dir`）。再加上 CLAUDE.md 里那段同步规则，机制 + 说明双保险。

三条命令都写成绝对路径：

```
python "$CLAUDE_PROJECT_DIR/.claude/hooks/tracker_hook.py" prompt
```

**别改成相对路径。** hook 命令是在 hook 进程的 cwd 下解析的，而 agent 只要在会话里 `cd` 进过一次子目录，cwd 就一直留在那儿：python 找不到脚本、以退出码 2 结束，而 UserPromptSubmit 的退出码 2 恰好是"拦截这条 prompt"——用户会突然发不出消息，SessionStart 则是静悄悄地不注入看板。hook shell 是 PowerShell 时把 `$CLAUDE_PROJECT_DIR` 换成 `$env:CLAUDE_PROJECT_DIR`。

## 文件

```
tracker.py            CLI + HTTP server（单文件）
index.html            看板（单页，无构建）
tracker.bat           Windows 一键启动
install.py            装进 Claude Code 项目的安装器（幂等）
hooks/tracker_hook.py Claude Code hooks
templates/            CLAUDE.md 段落、settings.json hooks、launch.json 模板
AGENTS.md             给 agent 的安装/使用说明
```

## English

A zero-dependency task tracker built around one question: *whose court is the ball in, and for how long?* Each active project holds "waits" (customer / vendor / internal / me) with age and nudge tracking, plus a timeline, notes and links. SQLite storage, a CLI, and a single-page web board.

It is designed to be maintained by an AI agent inside Claude Code: `install.py` drops the tracker into a project along with three hooks (SessionStart injects the board, UserPromptSubmit reminds the agent to sync on every message, Stop blocks once if a progress-like message ended without a tracker write) and a CLAUDE.md section describing the sync rules. Paste the repo URL to your agent and say "set this up"; it will follow [AGENTS.md](AGENTS.md).

UI and CLI output are in Chinese.

## License

MIT
