#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whose-ball tracker 的 Claude Code hooks（零依赖）。

在 .claude/settings.json 里这样挂（hook 的 cwd 就是项目根目录）：

  SessionStart      -> python .claude/hooks/tracker_hook.py session-start
  UserPromptSubmit  -> python .claude/hooks/tracker_hook.py prompt
  Stop              -> python .claude/hooks/tracker_hook.py stop

三个子命令分别做：
  session-start  把 `tracker.py list` 的看板注入会话上下文，agent 一开场就知道球在谁手里
  prompt         每条用户消息附一句提醒：有进展就本回合写 tracker；同时记下时间戳和"像不像进展"
  stop           严格模式：用户消息像进展、但本回合 tracker.db 没被写过 -> 拦一次，让 agent 补写

配置文件 .claude/tracker_hooks.json（可选，缺省值见 DEFAULTS）。
状态文件放在系统临时目录，按 session_id 区分，不会进 git。
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.normcase(os.path.realpath(os.getcwd()))
CFG_PATH = os.path.join(ROOT, ".claude", "tracker_hooks.json")

DEFAULTS = {
    # tracker.py / tracker.db 所在目录，相对项目根
    "tracker_dir": ".",
    # 严格模式：Stop 时发现"像进展但没写 tracker"就拦一次
    "strict": True,
    # 用户消息长度 >= min_chars 视为"可能含进展"
    "min_chars": 60,
    # 或者命中任一关键词（不分大小写）
    "keywords": [
        "回了", "回复", "报价", "下单", "到了", "发了", "催", "签了", "放弃", "成单", "新询盘", "新项目",
        "客户", "供应商", "进展", "更新", "确认", "同意", "拒绝", "付款", "发货", "交期",
        "replied", "quote", "quoted", "order", "po ", "po#", "paid", "shipped", "confirmed",
        "declined", "won", "lost", "new inquiry", "update", "progress", "follow up",
    ],
}


def load_cfg():
    cfg = dict(DEFAULTS)
    try:
        with open(CFG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def cli(cfg):
    d = cfg["tracker_dir"].strip("/").strip("\\")
    return "python tracker.py" if d in ("", ".") else f"python {d}/tracker.py"


def paths(cfg):
    tdir = os.path.normpath(os.path.join(ROOT, cfg["tracker_dir"]))
    return os.path.join(tdir, "tracker.py"), os.path.join(tdir, "tracker.db")


def state_path(session_id):
    key = hashlib.sha1(ROOT.encode("utf-8")).hexdigest()[:10]
    d = os.path.join(tempfile.gettempdir(), "whose-ball")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{key}_{session_id or 'nosession'}.json")


def read_stdin():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", "replace")
        return json.loads(raw or "{}")
    except (ValueError, AttributeError):
        return {}


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()


RULES = (
    "同步规则（强制）：以下任一情况，必须在同一回合内用 CLI 写进 tracker，不要先问，"
    "写完在回复末尾一句话说明写了什么："
    "①用户告知某项目有进展 ②你替用户完成了动作 ③出现新项目 ④项目完成/放弃。"
    "动手前先 `{cli} show 代号` 看一眼；球转手用 `{cli} wait 代号 customer|vendor|internal|me \"等什么\" --who 人名 --clear`。"
)


def hook_session_start(cfg):
    tracker_py, _ = paths(cfg)
    if not os.path.exists(tracker_py):
        return
    try:
        out = subprocess.run(
            [sys.executable, tracker_py, "list"], capture_output=True, timeout=20
        ).stdout.decode("utf-8", "replace").replace("\r\n", "\n").strip()
    except (OSError, subprocess.SubprocessError) as e:
        out = f"(tracker list 失败: {e!r})"
    ctx = (
        f"[whose-ball tracker] 当前看板（`{cli(cfg)} list`）：\n{out or '(暂无项目)'}\n\n"
        + RULES.format(cli=cli(cfg))
    )
    emit({"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}})


def looks_like_update(cfg, prompt):
    if len(prompt.strip()) >= int(cfg["min_chars"]):
        return True
    low = prompt.lower()
    return any(k.lower() in low for k in cfg["keywords"])


def hook_prompt(cfg):
    inp = read_stdin()
    prompt = inp.get("prompt") or ""
    flagged = looks_like_update(cfg, prompt)
    try:
        with open(state_path(inp.get("session_id")), "w", encoding="utf-8") as f:
            json.dump({"prompt_ts": time.time(), "flagged": flagged}, f)
    except OSError:
        pass
    c = cli(cfg)
    ctx = (
        f"[tracker] 若本条消息含：项目进展 / 要你替用户完成的动作 / 新项目 / 完成或放弃 —— "
        f"本回合内先 `{c} show 代号`，再用 `{c} log|wait|add|close` 写入，不要先问；回复末尾一句话说明写了什么。"
    )
    emit({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": ctx}})


def hook_stop(cfg):
    inp = read_stdin()
    if inp.get("stop_hook_active") or not cfg.get("strict", True):
        return
    try:
        with open(state_path(inp.get("session_id")), encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        return
    if not st.get("flagged"):
        return
    _, db = paths(cfg)
    mtime = os.path.getmtime(db) if os.path.exists(db) else 0
    if mtime >= float(st.get("prompt_ts", 0)):
        return  # 本回合已经写过 tracker
    c = cli(cfg)
    emit({
        "decision": "block",
        "reason": (
            "[whose-ball tracker 严格模式] 本回合用户消息看起来含有项目进展，但 tracker.db 没有被写入。"
            "自查：本回合是否出现了「用户告知进展 / 你替用户完成了动作 / 新项目 / 完成或放弃」之一？"
            f"是 → 现在就用 `{c} log|wait|add|close ...` 写入，并一句话说明。"
            "否 → 直接结束，不要解释，不要道歉。"
        ),
    })


def main():
    sub = sys.argv[1] if len(sys.argv) > 1 else ""
    cfg = load_cfg()
    if sub == "session-start":
        hook_session_start(cfg)
    elif sub == "prompt":
        hook_prompt(cfg)
    elif sub == "stop":
        hook_stop(cfg)
    else:
        sys.exit("usage: tracker_hook.py session-start|prompt|stop")


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    main()
