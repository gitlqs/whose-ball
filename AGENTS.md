# AGENTS.md —— 给 AI agent 的安装与使用说明

> 用户把这个 repo 链接发给你，说"我要用这个工具，帮我 setup 好"时，按本文操作。
> 目标：把 whose-ball tracker 装进**当前 Claude Code 会话所在的项目目录**，并用 hooks 把"用户一有进展就同步 tracker"的行为钉死。
> 全程不要问用户问题，能推断的自己定，装完一次性汇报。

## 0. 这是什么

whose-ball（球在谁手里）是一个零依赖的 Python 任务跟踪器：每个进行中的项目记录**当前在等谁、等什么、等了几天**，加时间线、笔记、链接。数据在本地 SQLite（tracker.db），有 CLI 和一个单页 web 看板。

它的价值不在于工具本身，而在于 **agent 每个回合都替用户维护它**：用户说一句"客户回了"，agent 就该写一条 log、把等待项转手。本 repo 附带三个 Claude Code hooks 来强制这件事。

## 1. 安装步骤（照做）

```bash
# 1) 确认 python（3.8+）。Windows 通常是 python，macOS/Linux 可能是 python3
python --version || python3 --version

# 2) 浅克隆到临时目录
git clone --depth 1 https://github.com/gitlqs/whose-ball.git /tmp/whose-ball
#    没有 git 就下 zip：curl -L https://github.com/gitlqs/whose-ball/archive/refs/heads/main.zip -o /tmp/wb.zip && unzip /tmp/wb.zip -d /tmp && mv /tmp/whose-ball-main /tmp/whose-ball

# 3) 装进当前项目根目录（= Claude Code 的工作目录）
python /tmp/whose-ball/install.py --target "$PWD" [--subdir tracker] [--stages "..."] [--categories "..."] [--no-strict]

# 4) 清理
rm -rf /tmp/whose-ball
```

`install.py` 是幂等的，重复跑不会重复追加。它做的事：

| 步骤 | 内容 |
|---|---|
| tracker 文件 | `tracker.py` `index.html` `tracker.bat` 复制到项目根（或 `--subdir`） |
| hook 脚本 | `hooks/tracker_hook.py` → `.claude/hooks/tracker_hook.py` |
| hook 配置 | 写 `.claude/tracker_hooks.json`（tracker 目录、严格模式开关、关键词） |
| settings.json | 把 SessionStart / UserPromptSubmit / Stop 三个 hook **合并**进 `.claude/settings.json`，不动已有 hook |
| launch.json | 加一个名为 `tracker` 的 preview 配置（端口 8765） |
| CLAUDE.md | 追加一段带 `<!-- whose-ball:start/end -->` 标记的"同步规则"段落 |
| .gitignore | 加 `tracker.db` `tracker.db.undo` |
| 自检 | 跑 `tracker.py list` 和三个 hook，确认 JSON 输出合法 |

### 参数怎么定（不要问用户，自己判断）

- `--subdir tracker`：项目根目录已有别的 `index.html`，或者这是个代码项目（有 package.json / src / pyproject 等）不想被杂物污染时用。纯工作区（只有文档、脚本）就装根目录，命令更短。
- `--stages`：流水线阶段名，逗号分隔，会写进 CLAUDE.md 和看板。按用户领域推断：
  - 销售/报价类：`询盘澄清,供应商询价,做报价,已报出,成单交付`
  - 招聘：`筛简历,初面,终面,offer,入职`
  - 通用/不确定：用默认 `待澄清,进行中,等回复,收尾`，装完告诉用户可以改
- `--categories`：类别名，默认 `主线,支持,内务,参考`，同上推断。
- `--no-strict`：关闭 Stop 严格检查。默认**开**。如果用户的会话大多是写代码而不是跟进事项，可以关掉，只留提醒。
- `--skip-claude-md`：项目 CLAUDE.md 里已经有手写的 tracker 段落（没有 `<!-- whose-ball -->` 标记）时用，避免出现两段。
- 已有 tracker.db 时不传 `--stages` 就不会覆盖 db 里的阶段设置；db 里没设过则写入默认值。
- 本机只有 `python3` 没有 `python`：装完把 `.claude/settings.json` 里三处 `python .claude/hooks/...` 改成 `python3 ...`。

### 装完怎么汇报

一次说清：装到哪了、hooks 需要**重开会话**才生效、看板怎么开（`python tracker.py serve` 或 preview 里的 `tracker`，Windows 双击 `tracker.bat`）、阶段/类别用了什么值以及怎么改（`python tracker.py set pipeline_stages "a,b,c"` + 改 CLAUDE.md 那一行）。然后问用户要不要现在把手头进行中的事项录进去，用户说一个你建一个。

## 2. Hooks 是怎么钉住行为的

三个 hook 都是 `.claude/hooks/tracker_hook.py` 的子命令，配置在 `.claude/tracker_hooks.json`：

| Hook | 触发 | 做什么 |
|---|---|---|
| **SessionStart** | 会话开始/恢复/compact 后 | 把 `tracker.py list` 的看板整个注入上下文，agent 开场就知道球在谁手里，不用再查 |
| **UserPromptSubmit** | 每条用户消息 | 附一句提醒："若本条含进展/动作/新项目/完成，本回合先 show 再 log/wait/add/close，不要先问"。同时记下时间戳，并按长度（≥ `min_chars`）或关键词判断这条消息"像不像进展" |
| **Stop** | agent 准备结束回合 | 严格模式：如果这条消息"像进展"，但 `tracker.db` 的 mtime 早于消息时间（即本回合没写过 tracker），就 block 一次，要求 agent 自查补写；确实没变化则直接结束。`stop_hook_active` 时放行，不会死循环 |

调参都在 `.claude/tracker_hooks.json`：

```json
{
  "tracker_dir": ".",          // tracker.py 所在目录，相对项目根
  "strict": true,              // 关掉就只剩提醒，不拦
  "min_chars": 60,             // 消息长度阈值
  "keywords": ["回了", "报价", "replied", "quote", "..."]   // 命中即视为"像进展"，可按领域替换
}
```

CLAUDE.md 里那段"同步规则"是第四道保险：hooks 靠机制，CLAUDE.md 靠说明，两者内容一致。

## 3. 装好之后，你（agent）每个回合要做的事

1. 用户提到某个项目 → 先 `python tracker.py show 代号`（唯一子串即可）看当前等待项和时间线。
2. 出现下面任一情况 → **同一回合内**写 tracker，不要问"要不要记"：
   - 用户告知进展：`log "代号" "谁 做了什么 具体数字/型号/日期"`
   - 球转手：`wait "代号" customer|vendor|internal|me "等什么" --who 人名 --clear`（`--clear` 先了结旧等待项）
   - 你替用户做完了一件事（发邮件、建记录、算数）：`log` + 通常跟一个 `wait`
   - 新事项：`add "代号" -t "谁/什么 · 一句话" -c 类别 -s 阶段`，顺手 `ref` 挂链接、`note "档案"` 存联系方式/需求
   - 完成或放弃：`close "代号" "原因"`
   - 阶段推进：`update "代号" -s 阶段`
3. 回复末尾一句话说明写了什么（例："已记 tracker：Acme 项目 log + 转为等客户回复报价"）。
4. 时间线要有信息量。写"Bob 报价 X-200 $4,150 EXW 交期 3 周"，不写"有更新"。

等待方（party）四选一：`customer` 等对方/客户，`vendor` 等供应商/第三方，`internal` 等内部同事，`me` 轮到用户自己动手。

## 4. 手动安装（install.py 跑不了时）

1. 把 `tracker.py` `index.html` `tracker.bat` 放进项目根（或子目录）。
2. `hooks/tracker_hook.py` 放到 `.claude/hooks/`。
3. 把 `templates/settings.hooks.json` 的内容合并进 `.claude/settings.json`（同名事件数组 append，别覆盖）。
4. `templates/launch.json` 的 `tracker` 配置合并进 `.claude/launch.json`，`{TRACKER_PY}` 换成实际路径。
5. `templates/CLAUDE.section.md` 追加到 `CLAUDE.md`，替换 `{CLI}` `{TRACKER_DIR}` `{STAGES}` `{CATEGORIES}`。
6. 写 `.claude/tracker_hooks.json`：`{"tracker_dir": ".", "strict": true}`。
7. `.gitignore` 加 `tracker.db` `tracker.db.undo`。
8. 验证：`echo '{"session_id":"t","prompt":"客户回了"}' | python .claude/hooks/tracker_hook.py prompt` 应输出一段 JSON。

## 5. CLI 速查

```
python tracker.py add 代号 -t "一句话" -c 类别 -s 阶段
python tracker.py wait 代号 customer|vendor|internal|me "等什么" --who 人名 [--since YYYY-MM-DD] [--clear]
python tracker.py done WAIT_ID ["备注"]
python tracker.py nudge WAIT_ID ["备注"]
python tracker.py log 代号 "发生了什么" [--ts "YYYY-MM-DD HH:MM"]
python tracker.py note 代号 "标题" "内容" | -f 文件.md      (同名标题覆盖)
python tracker.py ref 代号 "标签" "URL或路径"
python tracker.py update 代号 [-t ...] [-c ...] [-s 阶段] [--status active|closed]
python tracker.py close 代号 ["原因"]
python tracker.py list [--all]        按等待方分组，超过 stale_days 的标 ⚠
python tracker.py show 代号 [-n 15]
python tracker.py set stale_days 5 | set pipeline_stages "a,b,c"
python tracker.py serve [--port 8765]
```
