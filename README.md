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
| projects | 项目：代号（唯一，支持子串匹配）、一句话身份、类别、阶段、部门（dept，可选）、背景（brief，2-3 行）、项目 session 链接、active/closed |
| waits | 等待项：party（customer/vendor/internal/me）、等谁、等什么、起始日、最后催促日、是否完成；可选邮件定位：mailbox（哪个邮箱）、search（能搜到那封邮件的搜索串）、mail_folder（inbox/drafts/sent）、mail_to（草稿实际收件人） |
| events | 时间线，所有操作自动落一条，`log` 手动加 |
| notes | 笔记，同名标题覆盖（适合"档案""需求"这种长期维护的） |
| refs | 链接/路径，带标签 |
| decisions | 提请人拍板的事：kind（choice 选项 / text 文字意见 / ack 知悉）、问题、选项、答复（answer_source=other 表示在别处定的）、答复时间、转交时间、作废时间和原因、提问者 |
| reviews | 落笔前审查记录：action（email / sf_create / sf_update）、计划、pass/fail、发现、审查人、时间；`review-ok` 供 hook 判断 |
| settings | `stale_days`（默认 5）、`pipeline_stages`（看板顶部流水线）、`departments`（部门表 JSON，`depts --set` 写）、`owner_name`（/boss 上"轮到 X"）、`draft_review_pattern`（/boss 识别待审草稿的关键词，`|` 分隔，默认 `审核发出`） |

全部存在 `tracker.db`（SQLite），和 `tracker.py` 同目录。web 写操作前自动快照到 `tracker.db.undo`，看板上可撤回一步。

## CLI

```
add 代号 -t "一句话" -c 类别 -s 阶段 [-d 部门]  新项目
wait 代号 customer|vendor|internal|me "等什么" --who 人名 [--since 日期] [--clear]
     [--mail 邮箱 --search '搜索串' [--folder inbox|drafts|sent] [--to 收件人]]
                                              球转手；--clear 先了结旧等待项；--mail/--search 让 /boss 卡片能一键复制搜索串
wait-mail WAIT_ID [--mail] [--search] [--folder] [--to]   给已有等待项补/改邮件定位
brief 代号 ["背景 2-3 行"] [--session 链接或id]  项目背景（/boss 卡片顶部），状态变了就更新
done WAIT_ID ["备注"]                          等待项完成
nudge WAIT_ID ["备注"]                         记一次催促（重置计时）
log 代号 "发生了什么" [--ts 时间]              时间线
note 代号 "标题" "内容" | -f 文件               笔记
ref 代号 "标签" "URL或路径"                     链接
update 代号 [-t] [-c] [-s 阶段] [-d 部门] [--status]  改字段
close 代号 ["原因"]                            关闭，自动了结所有等待项
list [--all] [--dept 部门|-]                  按等待方分组，超期标 ⚠；- = 未分部门
show 代号 [-n 15]                              详情 + 最近时间线
set key value                                 stale_days / pipeline_stages
serve [--port 8765]                           web 看板（/ 项目看板，/boss 决策看板）

depts [--set "CODE=名称;CODE=名称"]            部门表（不配置则 dept 不校验）
ask 代号 "问题" --options "A|B|C" | --text | --ack [--by 提问者]   提请决策
decisions [--pending | --answered-unconsumed | --voided] 列决策
answer ID ["答复"]                             答复（等同 /boss 上点按钮）
answer ID --other "自由文本"                  记录在别处（项目 session 里）口头定的答案，不必匹配预设选项，标为 other
void ID "原因"                                作废：不再需要答复；/boss 与 decisions --pending 不再列出（close 项目会自动作废其未答决策）
consume ID                                    标记答复已转交处理
review 代号 --action email|sf_create|sf_update --verdict pass|fail [--plan-file F | --plan T] [--findings ...] [--reviewer ...]
review-ok 代号 --action X [--within 30]        最近一条审查是 pass 且在 N 分钟内 → 退出码 0，否则 1
```

### /boss 决策看板

给"只想看要我做什么"的人：顶部按部门统计 轮到我 / 内部在做 / 等外部；下面每张卡都是 标题（部门 · 项目一句话）/ 背景 / 进展（最近 4 条，可展开全部）/ 现在（阶段 + 所有未完成等待项）/ 要你做 / 邮件（邮箱、文件夹、收件人、一键复制的搜索串）/ 链接（refs 里的网址 + 项目 session）。分组：待审草稿（party=me 且 what 命中 `draft_review_pattern`，带「已发」按钮 = done + 记时间线）、待决定（choice/ack 按钮）、待给意见（文本框）、其他轮到我、已答待转交。回写走 POST /api，写 `decisions.answer/answered_at`；agent 读 `decisions --answered-unconsumed` 处理后 `consume`。每 60 秒自动刷新，手机宽度可用。

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
boss.html             决策看板 /boss（单页，无构建）
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
