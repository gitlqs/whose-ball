#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""通用 task tracker：项目 / 等待项 / 时间线 / 参考资料。

零依赖（Python 3.8+ stdlib），数据存本目录 tracker.db。

  python tracker.py add 代号 -t "一句话" -c 类别 -s 阶段
  python tracker.py wait 代号 customer|vendor|internal|me "等什么事" --who 人名 [--clear]
  python tracker.py done WAIT_ID [备注]
  python tracker.py nudge WAIT_ID
  python tracker.py log 代号 "发生了什么"
  python tracker.py note 代号 "标题" -f 文件.md   (或直接给内容)
  python tracker.py ref 代号 "标签" "URL或路径"
  python tracker.py update 代号 [-t ...] [-s 阶段] [--status active|closed]
  python tracker.py close 代号 ["原因"]
  python tracker.py list [--all] / show 代号 / serve [--port 8765]
"""
import argparse
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime, date

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "tracker.db")
PARTIES = ("customer", "vendor", "internal", "me")
PARTY_CN = {"customer": "等客户", "vendor": "等供应商", "internal": "等内部同事", "me": "轮到我"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects(
  id INTEGER PRIMARY KEY,
  code TEXT UNIQUE NOT NULL,
  title TEXT DEFAULT '',
  category TEXT DEFAULT '',
  stage TEXT DEFAULT '',
  status TEXT DEFAULT 'active',
  created_at TEXT, updated_at TEXT, closed_at TEXT
);
CREATE TABLE IF NOT EXISTS waits(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  party TEXT NOT NULL,
  who TEXT DEFAULT '',
  what TEXT NOT NULL,
  since TEXT,
  nudged_at TEXT,
  done INTEGER DEFAULT 0,
  done_at TEXT,
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  ts TEXT, text TEXT
);
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  title TEXT, content TEXT, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS refs(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  label TEXT, target TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today():
    return date.today().isoformat()


def get_setting(conn, key, default):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def find_project(conn, code, required=True):
    """精确匹配优先，否则唯一子串匹配（不分大小写）。"""
    row = conn.execute("SELECT * FROM projects WHERE code=?", (code,)).fetchone()
    if row:
        return row
    rows = conn.execute(
        "SELECT * FROM projects WHERE code LIKE ? COLLATE NOCASE", ("%" + code + "%",)
    ).fetchall()
    if len(rows) == 1:
        return rows[0]
    if required:
        if not rows:
            sys.exit(f"找不到项目: {code}")
        sys.exit(f"'{code}' 匹配到多个项目: " + ", ".join(r["code"] for r in rows))
    return None


def add_event(conn, pid, text, ts=None):
    conn.execute(
        "INSERT INTO events(project_id, ts, text) VALUES(?,?,?)", (pid, ts or now(), text)
    )
    conn.execute("UPDATE projects SET updated_at=? WHERE id=?", (now(), pid))


# ---------- 核心操作（CLI 与 HTTP 共用） ----------

def op_add(conn, code, title="", category="", stage="", quiet=False):
    if conn.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
        sys.exit(f"项目已存在: {code}")
    cur = conn.execute(
        "INSERT INTO projects(code,title,category,stage,created_at,updated_at) VALUES(?,?,?,?,?,?)",
        (code, title, category, stage, now(), now()),
    )
    add_event(conn, cur.lastrowid, "项目创建")
    conn.commit()
    if not quiet:
        print(f"已创建项目 [{code}] {title}")
    return cur.lastrowid


def op_update(conn, code, title=None, category=None, stage=None, status=None):
    p = find_project(conn, code)
    changes = []
    for field, val in (("title", title), ("category", category)):
        if val is not None and val != p[field]:
            conn.execute(f"UPDATE projects SET {field}=? WHERE id=?", (val, p["id"]))
            changes.append(f"{field}: {val}")
    if stage is not None and stage != p["stage"]:
        conn.execute("UPDATE projects SET stage=? WHERE id=?", (stage, p["id"]))
        changes.append(f"阶段 {p['stage'] or '—'} → {stage}")
    if status is not None and status != p["status"]:
        closed_at = now() if status == "closed" else None
        conn.execute(
            "UPDATE projects SET status=?, closed_at=? WHERE id=?", (status, closed_at, p["id"])
        )
        changes.append("重新打开" if status == "active" else "关闭项目")
        if status == "closed":  # 关单时了结所有未完成等待项
            conn.execute(
                "UPDATE waits SET done=1, done_at=? WHERE project_id=? AND done=0",
                (now(), p["id"]),
            )
    for c in changes:
        add_event(conn, p["id"], c)
    conn.commit()
    print(f"[{p['code']}] " + ("; ".join(changes) if changes else "无变化"))


def op_wait(conn, code, party, what, who="", since=None, clear=False, quiet=False):
    if party not in PARTIES:
        sys.exit(f"party 必须是 {'/'.join(PARTIES)} 之一")
    p = find_project(conn, code)
    if clear:
        conn.execute(
            "UPDATE waits SET done=1, done_at=? WHERE project_id=? AND done=0", (now(), p["id"])
        )
    cur = conn.execute(
        "INSERT INTO waits(project_id,party,who,what,since,created_at) VALUES(?,?,?,?,?,?)",
        (p["id"], party, who, what, since or today(), now()),
    )
    label = PARTY_CN[party] + (f"({who})" if who else "")
    add_event(conn, p["id"], f"{label}: {what}")
    conn.commit()
    if not quiet:
        print(f"[{p['code']}] wait#{cur.lastrowid} {label}: {what}")
    return cur.lastrowid


def op_done(conn, wait_id, note=""):
    w = conn.execute("SELECT * FROM waits WHERE id=?", (wait_id,)).fetchone()
    if not w:
        sys.exit(f"找不到等待项 #{wait_id}")
    conn.execute("UPDATE waits SET done=1, done_at=? WHERE id=?", (now(), wait_id))
    add_event(conn, w["project_id"], f"完成: {w['what']}" + (f" — {note}" if note else ""))
    conn.commit()
    print(f"wait#{wait_id} 已完成: {w['what']}")


def op_nudge(conn, wait_id, note=""):
    w = conn.execute("SELECT * FROM waits WHERE id=?", (wait_id,)).fetchone()
    if not w:
        sys.exit(f"找不到等待项 #{wait_id}")
    conn.execute("UPDATE waits SET nudged_at=? WHERE id=?", (today(), wait_id))
    add_event(conn, w["project_id"], f"催促 {w['who'] or PARTY_CN[w['party']]}: {w['what']}"
              + (f" — {note}" if note else ""))
    conn.commit()
    print(f"wait#{wait_id} 已记录催促 ({today()})")


def op_log(conn, code, text, ts=None):
    p = find_project(conn, code)
    add_event(conn, p["id"], text, ts)
    conn.commit()
    print(f"[{p['code']}] 已记录: {text}")


def op_note(conn, code, title, content):
    p = find_project(conn, code)
    row = conn.execute(
        "SELECT id FROM notes WHERE project_id=? AND title=?", (p["id"], title)
    ).fetchone()
    if row:
        conn.execute("UPDATE notes SET content=?, updated_at=? WHERE id=?", (content, now(), row["id"]))
        verb = "已更新"
    else:
        conn.execute(
            "INSERT INTO notes(project_id,title,content,created_at,updated_at) VALUES(?,?,?,?,?)",
            (p["id"], title, content, now(), now()),
        )
        verb = "已添加"
    conn.commit()
    print(f"[{p['code']}] 笔记{verb}: {title}")


def op_ref(conn, code, label, target):
    p = find_project(conn, code)
    conn.execute(
        "INSERT INTO refs(project_id,label,target,created_at) VALUES(?,?,?,?)",
        (p["id"], label, target, now()),
    )
    conn.commit()
    print(f"[{p['code']}] 参考{label}: {target}")


# ---------- 展示 ----------

def wait_age(w, ref_day=None):
    """从(最后催促日或起始日)到今天的天数。"""
    start = w["nudged_at"] or w["since"]
    try:
        d0 = date.fromisoformat(start[:10])
    except (TypeError, ValueError):
        return 0
    return ((ref_day or date.today()) - d0).days


def cmd_list(args):
    conn = db()
    where = "" if args.all else "WHERE p.status='active'"
    rows = conn.execute(f"SELECT * FROM projects p {where} ORDER BY updated_at DESC").fetchall()
    waits = {}
    for w in conn.execute("SELECT * FROM waits WHERE done=0"):
        waits.setdefault(w["project_id"], []).append(w)
    stale = int(get_setting(conn, "stale_days", "5"))
    groups = {k: [] for k in PARTIES}
    idle = []
    for p in rows:
        ws = waits.get(p["id"], [])
        if not ws:
            idle.append(p)
        for w in ws:
            groups[w["party"]].append((p, w))
    for party in ("me", "customer", "vendor", "internal"):
        items = groups[party]
        if not items:
            continue
        print(f"\n== {PARTY_CN[party]} ({len(items)}) ==")
        for p, w in items:
            age = wait_age(w)
            mark = " ⚠" if age >= stale else ""
            who = f" @{w['who']}" if w["who"] else ""
            print(f"  #{w['id']:<3} [{p['code']}]{who} {w['what']}  ({age}天{mark})")
    if idle:
        print(f"\n== 无等待项 ({len(idle)}) ==")
        for p in idle:
            print(f"  [{p['code']}] {p['title']}  ({p['status']})")
    if not rows:
        print("暂无项目")


def cmd_show(args):
    conn = db()
    p = find_project(conn, args.code)
    print(f"[{p['code']}] {p['title']}")
    print(f"  类别: {p['category'] or '—'}  阶段: {p['stage'] or '—'}  状态: {p['status']}")
    for w in conn.execute(
        "SELECT * FROM waits WHERE project_id=? AND done=0 ORDER BY id", (p["id"],)
    ):
        who = f" @{w['who']}" if w["who"] else ""
        nudge = f", {w['nudged_at']}催过" if w["nudged_at"] else ""
        print(f"  wait#{w['id']} {PARTY_CN[w['party']]}{who}: {w['what']} (自{w['since']}{nudge}, {wait_age(w)}天)")
    for r in conn.execute("SELECT * FROM refs WHERE project_id=? ORDER BY id", (p["id"],)):
        print(f"  ref: {r['label']} → {r['target']}")
    for n in conn.execute("SELECT title FROM notes WHERE project_id=? ORDER BY id", (p["id"],)):
        print(f"  note: {n['title']}")
    print("  -- 时间线 --")
    for e in conn.execute(
        "SELECT * FROM events WHERE project_id=? ORDER BY ts DESC, id DESC LIMIT ?",
        (p["id"], args.n),
    ):
        print(f"  {e['ts']}  {e['text']}")


# ---------- HTTP ----------

def full_state(conn):
    state = {"today": today(),
             "stale_days": int(get_setting(conn, "stale_days", "5")),
             "pipeline_stages": get_setting(
                 conn, "pipeline_stages", "询盘澄清,供应商询价,做报价,已报出,成单交付").split(","),
             "projects": []}
    for p in conn.execute("SELECT * FROM projects ORDER BY updated_at DESC"):
        d = dict(p)
        d["waits"] = [dict(w) | {"age": wait_age(w)} for w in conn.execute(
            "SELECT * FROM waits WHERE project_id=? ORDER BY done, id DESC", (p["id"],))]
        d["events"] = [dict(e) for e in conn.execute(
            "SELECT * FROM events WHERE project_id=? ORDER BY ts DESC, id DESC LIMIT 50", (p["id"],))]
        d["notes"] = [dict(n) for n in conn.execute(
            "SELECT * FROM notes WHERE project_id=? ORDER BY id", (p["id"],))]
        d["refs"] = [dict(r) for r in conn.execute(
            "SELECT * FROM refs WHERE project_id=? ORDER BY id", (p["id"],))]
        state["projects"].append(d)
    return state


def cmd_serve(args):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    index_path = os.path.join(BASE, "index.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(index_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path == "/api/state":
                conn = db()
                try:
                    self._send(200, full_state(conn))
                finally:
                    conn.close()
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(n) or b"{}")
                action, a = req.get("action"), req.get("args", {})
                undo_path = DB_PATH + ".undo"
                if action == "undo":
                    if not os.path.exists(undo_path):
                        self._send(400, {"error": "没有可撤回的操作"})
                        return
                    shutil.copyfile(undo_path, DB_PATH)
                    os.remove(undo_path)  # 只允许撤回一步
                    conn = db()
                    try:
                        self._send(200, full_state(conn))
                    finally:
                        conn.close()
                    return
                shutil.copyfile(DB_PATH, undo_path)  # 写操作前快照
                conn = db()
                try:
                    dispatch = {
                        "add": lambda: op_add(conn, a["code"], a.get("title", ""),
                                              a.get("category", ""), a.get("stage", ""), quiet=True),
                        "update": lambda: op_update(conn, a["code"], a.get("title"),
                                                    a.get("category"), a.get("stage"), a.get("status")),
                        "wait": lambda: op_wait(conn, a["code"], a["party"], a["what"], a.get("who", ""),
                                                a.get("since"), a.get("clear", False), quiet=True),
                        "done": lambda: op_done(conn, a["id"], a.get("note", "")),
                        "nudge": lambda: op_nudge(conn, a["id"], a.get("note", "")),
                        "log": lambda: op_log(conn, a["code"], a["text"]),
                        "note": lambda: op_note(conn, a["code"], a["title"], a["content"]),
                        "ref": lambda: op_ref(conn, a["code"], a["label"], a["target"]),
                    }
                    if action not in dispatch:
                        self._send(400, {"error": f"unknown action {action}"})
                        return
                    dispatch[action]()
                    self._send(200, full_state(conn))
                finally:
                    conn.close()
            except SystemExit as e:  # op_* 用 sys.exit 报业务错误
                self._send(400, {"error": str(e)})
            except Exception as e:
                self._send(500, {"error": repr(e)})

        def log_message(self, *_):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"tracker 已启动: http://127.0.0.1:{args.port}  (Ctrl+C 停止)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


# ---------- 入口 ----------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="新建项目")
    s.add_argument("code"); s.add_argument("-t", "--title", default="")
    s.add_argument("-c", "--category", default=""); s.add_argument("-s", "--stage", default="")
    s.set_defaults(f=lambda a: op_add(db(), a.code, a.title, a.category, a.stage))

    s = sub.add_parser("update", help="更新项目字段")
    s.add_argument("code"); s.add_argument("-t", "--title"); s.add_argument("-c", "--category")
    s.add_argument("-s", "--stage"); s.add_argument("--status", choices=["active", "closed"])
    s.set_defaults(f=lambda a: op_update(db(), a.code, a.title, a.category, a.stage, a.status))

    s = sub.add_parser("close", help="关闭项目")
    s.add_argument("code"); s.add_argument("reason", nargs="?", default="")
    def _close(a):
        conn = db(); p = find_project(conn, a.code)
        if a.reason:
            add_event(conn, p["id"], a.reason); conn.commit()
        op_update(conn, p["code"], status="closed")
    s.set_defaults(f=_close)

    s = sub.add_parser("wait", help="添加等待项(球在谁手里)")
    s.add_argument("code"); s.add_argument("party", choices=PARTIES); s.add_argument("what")
    s.add_argument("--who", default=""); s.add_argument("--since")
    s.add_argument("--clear", action="store_true", help="先了结该项目所有未完成等待项")
    s.set_defaults(f=lambda a: op_wait(db(), a.code, a.party, a.what, a.who, a.since, a.clear))

    s = sub.add_parser("done", help="完成等待项")
    s.add_argument("id", type=int); s.add_argument("note", nargs="?", default="")
    s.set_defaults(f=lambda a: op_done(db(), a.id, a.note))

    s = sub.add_parser("nudge", help="记录催促")
    s.add_argument("id", type=int); s.add_argument("note", nargs="?", default="")
    s.set_defaults(f=lambda a: op_nudge(db(), a.id, a.note))

    s = sub.add_parser("log", help="记时间线事件")
    s.add_argument("code"); s.add_argument("text"); s.add_argument("--ts")
    s.set_defaults(f=lambda a: op_log(db(), a.code, a.text, a.ts))

    s = sub.add_parser("note", help="添加/更新笔记(同名标题覆盖)")
    s.add_argument("code"); s.add_argument("title")
    s.add_argument("content", nargs="?"); s.add_argument("-f", "--file")
    def _note(a):
        content = open(a.file, encoding="utf-8").read() if a.file else a.content
        if content is None:
            sys.exit("需要给出内容或 -f 文件")
        op_note(db(), a.code, a.title, content)
    s.set_defaults(f=_note)

    s = sub.add_parser("ref", help="添加参考链接/路径")
    s.add_argument("code"); s.add_argument("label"); s.add_argument("target")
    s.set_defaults(f=lambda a: op_ref(db(), a.code, a.label, a.target))

    s = sub.add_parser("list", help="按等待方分组列出")
    s.add_argument("--all", action="store_true", help="含已关闭项目")
    s.set_defaults(f=cmd_list)

    s = sub.add_parser("show", help="项目详情")
    s.add_argument("code"); s.add_argument("-n", type=int, default=15)
    s.set_defaults(f=cmd_show)

    s = sub.add_parser("set", help="设置项(如 stale_days)")
    s.add_argument("key"); s.add_argument("value")
    def _set(a):
        conn = db()
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (a.key, a.value))
        conn.commit(); print(f"{a.key} = {a.value}")
    s.set_defaults(f=_set)

    s = sub.add_parser("serve", help="启动 web 界面")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(f=cmd_serve)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
