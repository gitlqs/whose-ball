#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 whose-ball tracker 装进一个 Claude Code 项目（幂等，可重复运行）。

    python install.py --target /path/to/project [--subdir tracker] [--no-strict]
                      [--stages "阶段1,阶段2,..."] [--categories "类别1,类别2"]

做的事：
  1. 复制 tracker.py / index.html / tracker.bat 到 <target>/<subdir>
  2. 复制 hooks/tracker_hook.py 到 <target>/.claude/hooks/
  3. 写 <target>/.claude/tracker_hooks.json（hook 配置）
  4. 合并 hooks 进 <target>/.claude/settings.json（不覆盖已有 hook）
  5. 合并 "tracker" 配置进 <target>/.claude/launch.json
  6. 把 templates/CLAUDE.section.md 追加进 <target>/CLAUDE.md（有标记则跳过）
  7. .gitignore 加 tracker.db / tracker.db.undo
  8. 自检：跑 tracker.py list 和三个 hook，确认输出合法
"""
import argparse
import io
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MARK_START, MARK_END = "<!-- whose-ball:start -->", "<!-- whose-ball:end -->"
DEFAULT_STAGES = "待澄清,进行中,等回复,收尾"
DEFAULT_CATEGORIES = "主线,支持,内务,参考"


def log(msg):
    print("  " + msg)


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def copy_file(src, dst, force):
    if os.path.exists(dst) and not force:
        if open(src, "rb").read() == open(dst, "rb").read():
            log(f"已是最新: {dst}")
        else:
            log(f"已存在且内容不同，跳过（加 --force 覆盖）: {dst}")
        return
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    shutil.copyfile(src, dst)
    log(f"写入: {dst}")


def merge_hooks(settings_path):
    settings = read_json(settings_path, {})
    tmpl = read_json(os.path.join(HERE, "templates", "settings.hooks.json"), {})["hooks"]
    hooks = settings.setdefault("hooks", {})
    added = []
    for event, groups in tmpl.items():
        existing = hooks.setdefault(event, [])
        flat = json.dumps(existing, ensure_ascii=False)
        if "tracker_hook.py" in flat:
            continue
        existing.extend(groups)
        added.append(event)
    write_json(settings_path, settings)
    log(f"settings.json hooks: " + (f"新增 {', '.join(added)}" if added else "已存在，未改动"))


def merge_launch(launch_path, tracker_py_rel):
    launch = read_json(launch_path, {"version": "0.0.1", "configurations": []})
    confs = launch.setdefault("configurations", [])
    if any(c.get("name") == "tracker" for c in confs):
        log("launch.json: 'tracker' 配置已存在，未改动")
        return
    tmpl = read_json(os.path.join(HERE, "templates", "launch.json"), {})
    conf = tmpl["configurations"][0]
    conf["runtimeArgs"] = [a.replace("{TRACKER_PY}", tracker_py_rel) for a in conf["runtimeArgs"]]
    confs.append(conf)
    write_json(launch_path, launch)
    log("launch.json: 新增 'tracker' 配置")


def merge_claude_md(path, subdir, cli, stages, categories):
    with open(os.path.join(HERE, "templates", "CLAUDE.section.md"), encoding="utf-8") as f:
        section = f.read()
    section = (section.replace("{TRACKER_DIR}", subdir if subdir != "." else ".")
                      .replace("{CLI}", cli)
                      .replace("{STAGES}", " / ".join(stages.split(",")))
                      .replace("{CATEGORIES}", " / ".join(categories.split(","))))
    body = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            body = f.read()
    if MARK_START in body and MARK_END in body:
        pre, rest = body.split(MARK_START, 1)
        _, post = rest.split(MARK_END, 1)
        new = pre + section.strip() + post
        verb = "已更新标记段落"
    else:
        new = (body.rstrip() + "\n\n" if body.strip() else "") + section.strip() + "\n"
        verb = "已追加段落"
    with open(path, "w", encoding="utf-8") as f:
        f.write(new)
    log(f"CLAUDE.md: {verb}")


def current_stages(db_path):
    if not os.path.exists(db_path):
        return ""
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT value FROM settings WHERE key='pipeline_stages'").fetchone()
        conn.close()
        return row[0] if row else ""
    except Exception:
        return ""


def ensure_gitignore(target, subdir):
    path = os.path.join(target, ".gitignore")
    if not os.path.exists(path) and not os.path.isdir(os.path.join(target, ".git")):
        log(".gitignore: 不是 git 仓库，跳过")
        return
    prefix = "" if subdir == "." else subdir.rstrip("/") + "/"
    wanted = [f"{prefix}tracker.db", f"{prefix}tracker.db.undo", "__pycache__/"]
    lines = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    missing = [w for w in wanted if w not in lines]
    if not missing:
        log(".gitignore: 已包含")
        return
    with open(path, "a", encoding="utf-8") as f:
        if lines and lines[-1].strip():
            f.write("\n")
        f.write("# whose-ball tracker\n" + "\n".join(missing) + "\n")
    log(f".gitignore: 新增 {', '.join(missing)}")


def run(cmd, cwd, stdin_text=None):
    p = subprocess.run(cmd, cwd=cwd, input=(stdin_text or "").encode("utf-8"),
                       capture_output=True, timeout=30)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def selfcheck(target, tracker_py_rel, hook_rel):
    ok = True
    rc, out, err = run([sys.executable, tracker_py_rel, "list"], target)
    log(f"tracker.py list → rc={rc}" + ("" if rc == 0 else f" stderr={err.strip()}"))
    ok &= rc == 0
    sample = json.dumps({"session_id": "selfcheck", "prompt": "客户回了邮件，报价已确认，等 PO", "stop_hook_active": False})
    for sub in ("session-start", "prompt", "stop"):
        rc, out, err = run([sys.executable, hook_rel, sub], target, sample)
        valid = True
        if out.strip():
            try:
                json.loads(out)
            except ValueError:
                valid = False
        status = "ok" if (rc == 0 and valid) else f"FAIL rc={rc} valid_json={valid} stderr={err.strip()[:200]}"
        log(f"hook {sub:<13} → {status}")
        ok &= rc == 0 and valid
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=os.getcwd(), help="项目根目录（默认当前目录）")
    ap.add_argument("--subdir", default=".", help="tracker 文件放在项目里的哪个子目录（默认根目录）")
    ap.add_argument("--no-strict", action="store_true", help="不装 Stop 严格检查（只留提醒）")
    ap.add_argument("--stages", default=None, help=f"阶段值，逗号分隔（默认 {DEFAULT_STAGES}；不传则不覆盖 db 里已有的值）")
    ap.add_argument("--categories", default=None, help=f"类别值，逗号分隔（默认 {DEFAULT_CATEGORIES}）")
    ap.add_argument("--skip-claude-md", action="store_true", help="不动 CLAUDE.md（已有手写的 tracker 段落时用）")
    ap.add_argument("--force", action="store_true", help="覆盖已有的 tracker.py/index.html/tracker.bat/hook")
    a = ap.parse_args()
    stages = a.stages or DEFAULT_STAGES
    categories = a.categories or DEFAULT_CATEGORIES

    target = os.path.abspath(a.target)
    subdir = a.subdir.strip("/\\") or "."
    dest = os.path.normpath(os.path.join(target, subdir))
    tracker_py_rel = "tracker.py" if subdir == "." else f"{subdir}/tracker.py"
    cli = f"python {tracker_py_rel}"
    hook_rel = ".claude/hooks/tracker_hook.py"

    print(f"安装 whose-ball tracker → {target}  (tracker 目录: {dest})")

    if subdir == "." and os.path.exists(os.path.join(target, "index.html")) and not a.force:
        existing = open(os.path.join(target, "index.html"), "rb").read()
        mine = open(os.path.join(HERE, "index.html"), "rb").read()
        if existing != mine:
            sys.exit("项目根目录已有别的 index.html，请改用 --subdir tracker 安装到子目录。")

    print("[1/7] tracker 文件")
    for name in ("tracker.py", "index.html", "tracker.bat"):
        copy_file(os.path.join(HERE, name), os.path.join(dest, name), a.force)

    print("[2/7] hook 脚本")
    copy_file(os.path.join(HERE, "hooks", "tracker_hook.py"), os.path.join(target, hook_rel), a.force)

    print("[3/7] hook 配置")
    cfg_path = os.path.join(target, ".claude", "tracker_hooks.json")
    cfg = read_json(cfg_path, {})
    cfg["tracker_dir"] = subdir
    cfg["strict"] = not a.no_strict
    write_json(cfg_path, cfg)
    log(f"tracker_hooks.json: tracker_dir={subdir} strict={cfg['strict']}")

    print("[4/7] settings.json")
    merge_hooks(os.path.join(target, ".claude", "settings.json"))

    print("[5/7] launch.json")
    merge_launch(os.path.join(target, ".claude", "launch.json"), tracker_py_rel)

    print("[6/7] CLAUDE.md")
    if a.skip_claude_md:
        log("CLAUDE.md: --skip-claude-md，未改动")
    else:
        merge_claude_md(os.path.join(target, "CLAUDE.md"), subdir, cli, stages, categories)
    if a.stages or not current_stages(os.path.join(dest, "tracker.db")):
        rc, _, err = run([sys.executable, tracker_py_rel, "set", "pipeline_stages", stages], target)
        log(f"pipeline_stages = {stages}" if rc == 0 else f"pipeline_stages 写入失败: {err.strip()}")
    else:
        log(f"pipeline_stages 保留 db 已有值: {current_stages(os.path.join(dest, 'tracker.db'))}")

    print("[7/7] .gitignore")
    ensure_gitignore(target, subdir)

    print("自检")
    ok = selfcheck(target, tracker_py_rel, hook_rel)
    print("\n完成。" if ok else "\n完成，但自检有失败项，见上。")
    print("提醒：hooks 在 Claude Code 里要重开会话（或 /hooks 重载）才生效；"
          "hook 命令用的是 `python`，若本机只有 `python3`，请把 .claude/settings.json 里三处 `python` 改成 `python3`。")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
