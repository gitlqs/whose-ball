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
  python tracker.py list [--all] [--dept D] / show 代号 / serve [--port 8765]
  python tracker.py brief 代号 ["背景 2-3 行"] [--session 链接或id]
  python tracker.py wait ... [--mail qt|tt --search '<查询>' [--folder inbox|drafts|sent] [--to 收件人]]
  python tracker.py wait-mail WAIT_ID [--mail ..] [--search ..] [--folder ..] [--to ..]

部门（可选，代码从配置读，不写死）:
  python tracker.py depts [--set "CODE=名称;CODE=名称"]   add/update 用 -d/--dept
决策（给人拍板，显示在 /boss）:
  python tracker.py ask 代号 "问题" --options "A|B|C" | --text | --ack
  python tracker.py decisions [--pending | --answered-unconsumed]
  python tracker.py answer ID "答复"      python tracker.py consume ID
落笔前审查记录:
  python tracker.py review 代号 --action email|sf_create|sf_update --verdict pass|fail
                           [--plan-file 文件 | --plan 文字] [--findings ...] [--reviewer ...]
  python tracker.py review-ok 代号 --action X [--within 30]   (退出码 0=有效通过, 1=没有)
看板: serve 后 / 是项目看板, /boss 是决策看板
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
CREATE TABLE IF NOT EXISTS decisions(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('choice','text','ack')),
  question TEXT NOT NULL,
  options TEXT DEFAULT '[]',
  answer TEXT,
  answered_at TEXT,
  consumed_at TEXT,
  created_by TEXT DEFAULT '',
  created_at TEXT
);
CREATE TABLE IF NOT EXISTS reviews(
  id INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  action TEXT NOT NULL CHECK(action IN ('email','sf_create','sf_update')),
  plan TEXT DEFAULT '',
  verdict TEXT NOT NULL CHECK(verdict IN ('pass','fail')),
  findings TEXT DEFAULT '',
  reviewer TEXT DEFAULT '',
  created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_reviews_lookup ON reviews(project_id, action, created_at);
"""
DECISION_KINDS = ("choice", "text", "ack")
REVIEW_ACTIONS = ("email", "sf_create", "sf_update")
# 旧库补列：(表, 列, 定义)
MIGRATIONS = [("projects", "dept", "TEXT DEFAULT ''"),
              ("projects", "brief", "TEXT DEFAULT ''"),      # 背景: 2-3 行, 什么事/谁/要什么, 状态变了就更新
              ("projects", "session", "TEXT DEFAULT ''"),    # 项目 session 链接(claude://...)
              ("waits", "mailbox", "TEXT DEFAULT ''"),       # 邮件在哪个邮箱(自由文本, 如 qt / tt)
              ("waits", "search", "TEXT DEFAULT ''"),        # 能定位那封邮件的邮箱搜索串
              ("waits", "mail_folder", "TEXT DEFAULT ''"),   # inbox / drafts / sent
              ("waits", "mail_to", "TEXT DEFAULT ''")]       # 草稿的实际收件人
MAIL_FOLDERS = ("inbox", "drafts", "sent", "")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    for table, col, ddl in MIGRATIONS:
        cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            conn.commit()
    return conn


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def today():
    return date.today().isoformat()


def get_setting(conn, key, default):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def nowsec():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_depts(conn):
    """部门表从 settings.departments 读：JSON [{"code":..,"name":..}]。没配置则为空(不校验)。"""
    try:
        v = json.loads(get_setting(conn, "departments", "[]"))
        return [d for d in v if isinstance(d, dict) and d.get("code")]
    except ValueError:
        return []


def norm_dept(conn, dept):
    """校验并规范化部门代码；None=不改，''=清空。配置了部门表时只接受表内代码(不分大小写)。"""
    if dept is None or dept == "":
        return dept
    depts = get_depts(conn)
    if not depts:
        return dept
    for d in depts:
        if d["code"].lower() == dept.lower():
            return d["code"]
    sys.exit(f"未知部门 '{dept}'，可选: " + ", ".join(d["code"] for d in depts))


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

def op_add(conn, code, title="", category="", stage="", quiet=False, dept=""):
    if conn.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
        sys.exit(f"项目已存在: {code}")
    dept = norm_dept(conn, dept or "")
    cur = conn.execute(
        "INSERT INTO projects(code,title,category,stage,dept,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (code, title, category, stage, dept, now(), now()),
    )
    add_event(conn, cur.lastrowid, "项目创建")
    conn.commit()
    if not quiet:
        print(f"已创建项目 [{code}] {title}")
    return cur.lastrowid


def op_update(conn, code, title=None, category=None, stage=None, status=None, dept=None):
    p = find_project(conn, code)
    changes = []
    dept = norm_dept(conn, dept)
    for field, val in (("title", title), ("category", category), ("dept", dept)):
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


def check_mail_args(mailbox, search, folder):
    if folder is not None and folder not in MAIL_FOLDERS:
        sys.exit("--folder 必须是 inbox / drafts / sent")
    if (folder or mailbox) and not search:
        sys.exit("给了 --mail/--folder 就要同时给 --search（能在邮箱里搜到那封邮件的搜索串）")


def op_wait(conn, code, party, what, who="", since=None, clear=False, quiet=False,
            mailbox="", search="", folder="", mail_to=""):
    if party not in PARTIES:
        sys.exit(f"party 必须是 {'/'.join(PARTIES)} 之一")
    check_mail_args(mailbox, search, folder or None)
    p = find_project(conn, code)
    if clear:
        conn.execute(
            "UPDATE waits SET done=1, done_at=? WHERE project_id=? AND done=0", (now(), p["id"])
        )
    cur = conn.execute(
        "INSERT INTO waits(project_id,party,who,what,since,created_at,mailbox,search,mail_folder,mail_to) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (p["id"], party, who, what, since or today(), now(), mailbox or "", search or "",
         folder or "", mail_to or ""),
    )
    label = PARTY_CN[party] + (f"({who})" if who else "")
    add_event(conn, p["id"], f"{label}: {what}")
    conn.commit()
    if not quiet:
        mail = f"  [{mailbox or '?'}{'/' + folder if folder else ''}] {search}" if search else ""
        print(f"[{p['code']}] wait#{cur.lastrowid} {label}: {what}{mail}")
    return cur.lastrowid


def op_wait_mail(conn, wait_id, mailbox=None, search=None, folder=None, mail_to=None):
    """给已有等待项补/改邮件定位信息（不记时间线）。"""
    w = conn.execute("SELECT * FROM waits WHERE id=?", (wait_id,)).fetchone()
    if not w:
        sys.exit(f"找不到等待项 #{wait_id}")
    new = {"mailbox": w["mailbox"] if mailbox is None else mailbox,
           "search": w["search"] if search is None else search,
           "mail_folder": w["mail_folder"] if folder is None else folder,
           "mail_to": w["mail_to"] if mail_to is None else mail_to}
    check_mail_args(new["mailbox"], new["search"], new["mail_folder"])
    conn.execute("UPDATE waits SET mailbox=?, search=?, mail_folder=?, mail_to=? WHERE id=?",
                 (new["mailbox"], new["search"], new["mail_folder"], new["mail_to"], wait_id))
    conn.commit()
    print(f"wait#{wait_id} 邮件: [{new['mailbox'] or '?'}/{new['mail_folder'] or '?'}] {new['search']}"
          + (f"  → {new['mail_to']}" if new["mail_to"] else ""))


def op_brief(conn, code, text=None, session=None):
    p = find_project(conn, code)
    if text is not None:
        conn.execute("UPDATE projects SET brief=? WHERE id=?", (text.strip(), p["id"]))
    if session is not None:
        sess = session.strip()
        if sess and "://" not in sess:  # 只给了 session id
            sess = "claude://claude.ai/epitaxy/" + sess
        conn.execute("UPDATE projects SET session=? WHERE id=?", (sess, p["id"]))
    conn.commit()
    p = find_project(conn, p["code"])
    print(f"[{p['code']}] 背景: {p['brief'] or '—'}" + (f"\n  session: {p['session']}" if p["session"] else ""))


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


def op_ask(conn, code, question, kind, options=None, created_by=""):
    if kind not in DECISION_KINDS:
        sys.exit(f"kind 必须是 {'/'.join(DECISION_KINDS)} 之一")
    opts = [o.strip() for o in (options or []) if o.strip()]
    if kind == "choice" and len(opts) < 2:
        sys.exit('choice 类决策至少要两个选项 (--options "A|B")')
    if kind != "choice":
        opts = []
    p = find_project(conn, code)
    cur = conn.execute(
        "INSERT INTO decisions(project_id,kind,question,options,created_by,created_at) VALUES(?,?,?,?,?,?)",
        (p["id"], kind, question, json.dumps(opts, ensure_ascii=False), created_by, now()),
    )
    add_event(conn, p["id"], f"提请决策#{cur.lastrowid}: {question}"
              + (f" [{' / '.join(opts)}]" if opts else ""))
    conn.commit()
    print(f"[{p['code']}] decision#{cur.lastrowid} ({kind}) {question}")
    return cur.lastrowid


def op_answer(conn, did, answer):
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
    if not d:
        sys.exit(f"找不到决策 #{did}")
    if d["consumed_at"]:
        sys.exit(f"决策 #{did} 已被转交处理，不能再改")
    answer = (answer or "").strip()
    if d["kind"] == "ack":
        answer = answer or "已知悉"
    if not answer:
        sys.exit("答复不能为空")
    opts = json.loads(d["options"] or "[]")
    if d["kind"] == "choice" and answer not in opts:
        sys.exit(f"'{answer}' 不在选项里: " + " / ".join(opts))
    conn.execute("UPDATE decisions SET answer=?, answered_at=? WHERE id=?", (answer, now(), did))
    add_event(conn, d["project_id"], f"决策#{did} 答复: {answer}（问: {d['question']}）")
    conn.commit()
    print(f"decision#{did} 已答复: {answer}")


def op_consume(conn, did):
    d = conn.execute("SELECT * FROM decisions WHERE id=?", (did,)).fetchone()
    if not d:
        sys.exit(f"找不到决策 #{did}")
    if not d["answered_at"]:
        sys.exit(f"决策 #{did} 还没答复")
    if d["consumed_at"]:
        sys.exit(f"决策 #{did} 已于 {d['consumed_at']} 转交过")
    conn.execute("UPDATE decisions SET consumed_at=? WHERE id=?", (now(), did))
    add_event(conn, d["project_id"], f"决策#{did} 已转交处理: {d['answer']}")
    conn.commit()
    print(f"decision#{did} 已标记转交 ({d['answer']})")


def op_review(conn, code, action, verdict, plan="", findings="", reviewer=""):
    if action not in REVIEW_ACTIONS:
        sys.exit(f"action 必须是 {'/'.join(REVIEW_ACTIONS)} 之一")
    if verdict not in ("pass", "fail"):
        sys.exit("verdict 必须是 pass/fail")
    p = find_project(conn, code)
    cur = conn.execute(
        "INSERT INTO reviews(project_id,action,plan,verdict,findings,reviewer,created_at) VALUES(?,?,?,?,?,?,?)",
        (p["id"], action, plan, verdict, findings, reviewer, nowsec()),
    )
    add_event(conn, p["id"], f"落笔审查#{cur.lastrowid} {action}: {verdict.upper()}"
              + (f" — {findings[:120]}" if findings else ""))
    conn.commit()
    print(f"[{p['code']}] review#{cur.lastrowid} {action} {verdict}")
    return cur.lastrowid


def review_ok(conn, code, action, within_min=30):
    """该项目该动作最近一条审查记录是 pass 且在 within_min 分钟内 → (True, 说明)。"""
    p = find_project(conn, code)
    r = conn.execute(
        "SELECT * FROM reviews WHERE project_id=? AND action=? ORDER BY created_at DESC, id DESC LIMIT 1",
        (p["id"], action),
    ).fetchone()
    if not r:
        return False, f"[{p['code']}] 没有 {action} 审查记录"
    age = (datetime.now() - datetime.fromisoformat(r["created_at"])).total_seconds() / 60
    if r["verdict"] != "pass":
        return False, f"[{p['code']}] 最近一条 {action} 审查 review#{r['id']} 是 FAIL ({r['created_at']})"
    if age > within_min:
        return False, f"[{p['code']}] review#{r['id']} 通过于 {r['created_at']}，已过 {age:.0f} 分钟 > {within_min}"
    return True, f"[{p['code']}] review#{r['id']} {action} PASS，{age:.0f} 分钟前 (≤{within_min})"


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
    conds, params = [], []
    if not args.all:
        conds.append("p.status='active'")
    if getattr(args, "dept", None) is not None:
        if args.dept in ("-", "none", ""):
            conds.append("COALESCE(p.dept,'')=''")
        else:
            conds.append("p.dept=?")
            params.append(norm_dept(conn, args.dept))
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    rows = conn.execute(f"SELECT * FROM projects p {where} ORDER BY updated_at DESC", params).fetchall()
    waits = {}
    shown = {p["id"] for p in rows}
    for w in conn.execute("SELECT * FROM waits WHERE done=0"):
        if w["project_id"] in shown:
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
    print(f"  部门: {p['dept'] or '—'}  类别: {p['category'] or '—'}  阶段: {p['stage'] or '—'}  状态: {p['status']}")
    if p["brief"]:
        print(f"  背景: {p['brief']}")
    if p["session"]:
        print(f"  session: {p['session']}")
    for w in conn.execute(
        "SELECT * FROM waits WHERE project_id=? AND done=0 ORDER BY id", (p["id"],)
    ):
        who = f" @{w['who']}" if w["who"] else ""
        nudge = f", {w['nudged_at']}催过" if w["nudged_at"] else ""
        print(f"  wait#{w['id']} {PARTY_CN[w['party']]}{who}: {w['what']} (自{w['since']}{nudge}, {wait_age(w)}天)")
        if w["search"]:
            print(f"      邮件 [{w['mailbox'] or '?'}/{w['mail_folder'] or '?'}] {w['search']}"
                  + (f"  → {w['mail_to']}" if w["mail_to"] else ""))
    for d in conn.execute(
        "SELECT * FROM decisions WHERE project_id=? AND consumed_at IS NULL ORDER BY id", (p["id"],)
    ):
        print("  " + fmt_decision(d, p["code"]))
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


def fmt_decision(d, code):
    opts = json.loads(d["options"] or "[]")
    s = f"decision#{d['id']} [{code}] ({d['kind']}) {d['question']}"
    if opts:
        s += "  选项: " + " / ".join(opts)
    if d["answered_at"]:
        s += f"  → {d['answer']} ({d['answered_at']})"
        s += f" [已转交 {d['consumed_at']}]" if d["consumed_at"] else " [待转交]"
    else:
        s += "  [待答]"
    return s


def cmd_decisions(args):
    conn = db()
    q = "SELECT d.*, p.code FROM decisions d JOIN projects p ON p.id=d.project_id"
    if args.pending:
        q += " WHERE d.answered_at IS NULL"
    elif args.answered_unconsumed:
        q += " WHERE d.answered_at IS NOT NULL AND d.consumed_at IS NULL"
    rows = conn.execute(q + " ORDER BY d.id").fetchall()
    for d in rows:
        print(fmt_decision(d, d["code"]))
    if not rows:
        print("（无）")


def cmd_depts(args):
    conn = db()
    if args.set is not None:
        depts = []
        for part in args.set.split(";"):
            if not part.strip():
                continue
            code, _, name = part.partition("=")
            depts.append({"code": code.strip(), "name": name.strip() or code.strip()})
        conn.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('departments',?)",
                     (json.dumps(depts, ensure_ascii=False),))
        conn.commit()
    depts = get_depts(conn)
    if not depts:
        print('未配置部门（dept 字段不做校验）。配置: tracker.py depts --set "CODE=名称;CODE=名称"')
        return
    counts = {}
    for r in conn.execute("SELECT COALESCE(dept,'') d, COUNT(*) n FROM projects WHERE status='active' GROUP BY 1"):
        counts[r["d"]] = r["n"]
    for d in depts:
        print(f"  {d['code']:<10} {d['name']}  (进行中 {counts.get(d['code'], 0)})")
    print(f"  {'(未分)':<10} 进行中 {counts.get('', 0)}")


def project_ctx(conn, pid, cache):
    """/boss 卡片用的项目上下文：背景、阶段、全部未完成等待项、时间线、链接。"""
    if pid in cache:
        return cache[pid]
    p = conn.execute("SELECT * FROM projects WHERE id=?", (pid,)).fetchone()
    refs = [dict(r) for r in conn.execute(
        "SELECT label, target FROM refs WHERE project_id=? ORDER BY id", (pid,))]
    session = p["session"] or next((r["target"] for r in refs if r["target"].startswith("claude://")), "")
    ctx = {
        "code": p["code"], "title": p["title"], "dept": p["dept"] or "", "stage": p["stage"] or "",
        "brief": p["brief"] or "", "session": session,
        "links": [r for r in refs if r["target"].startswith(("http://", "https://"))],
        "waits": [{"id": w["id"], "party": w["party"], "party_cn": PARTY_CN[w["party"]], "who": w["who"],
                   "what": w["what"], "age": wait_age(w)} for w in conn.execute(
            "SELECT * FROM waits WHERE project_id=? AND done=0 ORDER BY id", (pid,))],
        "events": [{"ts": e["ts"], "text": e["text"]} for e in conn.execute(
            "SELECT ts, text FROM events WHERE project_id=? AND text NOT LIKE 'dept: %' "
            "ORDER BY ts DESC, id DESC LIMIT 40", (pid,))],
    }
    cache[pid] = ctx
    return ctx


def boss_state(conn):
    """/boss 页面数据：部门统计 + 待审草稿 + 待决定 + 待意见 + 其他轮到我。"""
    pat = [x for x in get_setting(conn, "draft_review_pattern", "审核发出").split("|") if x]
    depts = get_depts(conn)
    known = {d["code"] for d in depts}
    rows = conn.execute(
        "SELECT w.*, p.code, p.title, p.dept FROM waits w JOIN projects p ON p.id=w.project_id "
        "WHERE w.done=0 AND p.status='active' ORDER BY w.since, w.id").fetchall()
    stats = {d["code"]: {"code": d["code"], "name": d["name"], "me": 0, "internal": 0, "external": 0}
             for d in depts}
    stats_other = {"code": "", "name": "未分部门", "me": 0, "internal": 0, "external": 0}
    drafts, other_me, cache = [], [], {}
    for w in rows:
        bucket = stats[w["dept"]] if w["dept"] in known else stats_other
        key = {"me": "me", "internal": "internal"}.get(w["party"], "external")
        bucket[key] += 1
        if w["party"] != "me":
            continue
        item = {"id": w["id"], "code": w["code"], "title": w["title"], "dept": w["dept"] or "",
                "who": w["who"], "what": w["what"], "since": w["since"], "age": wait_age(w),
                "mailbox": w["mailbox"], "search": w["search"], "mail_folder": w["mail_folder"],
                "mail_to": w["mail_to"], "project": project_ctx(conn, w["project_id"], cache)}
        (drafts if any(k in w["what"] for k in pat) else other_me).append(item)
    stat_list = list(stats.values())
    if any(stats_other[k] for k in ("me", "internal", "external")) or not depts:
        stat_list.append(stats_other)
    decs = []
    for d in conn.execute(
            "SELECT d.*, p.code, p.title, p.dept FROM decisions d JOIN projects p ON p.id=d.project_id "
            "WHERE d.consumed_at IS NULL AND p.status='active' ORDER BY d.id"):
        x = dict(d)
        x["options"] = json.loads(d["options"] or "[]")
        x["project"] = project_ctx(conn, d["project_id"], cache)
        decs.append(x)
    return {
        "now": now(), "stale_days": int(get_setting(conn, "stale_days", "5")),
        "owner": get_setting(conn, "owner_name", "我"),
        "depts": depts, "stats": stat_list, "drafts": drafts, "other_me": other_me,
        "choices": [d for d in decs if not d["answered_at"] and d["kind"] in ("choice", "ack")],
        "texts": [d for d in decs if not d["answered_at"] and d["kind"] == "text"],
        "answered": [d for d in decs if d["answered_at"]],
    }


def op_sent(conn, wait_id):
    """/boss「已发」：了结等待项并记一笔时间线。"""
    w = conn.execute("SELECT * FROM waits WHERE id=?", (wait_id,)).fetchone()
    if not w:
        sys.exit(f"找不到等待项 #{wait_id}")
    if w["done"]:
        sys.exit(f"wait#{wait_id} 已经完成过了")
    op_done(conn, wait_id, "已发")
    add_event(conn, w["project_id"], f"在 /boss 标记已发出: {w['what']}")
    conn.commit()


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
    boss_path = os.path.join(BASE, "boss.html")

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json; charset=utf-8"):
            data = body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            # 分块写：本机(Windows)上一次 sendall 超过约 512KB 会卡 19 秒后被对端重置，分块就没事
            for i in range(0, len(data), 65536):
                self.wfile.write(data[i:i + 65536])

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                with open(index_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.split("?")[0] in ("/boss", "/boss.html"):
                with open(boss_path, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            elif self.path.split("?")[0] == "/api/boss":
                conn = db()
                try:
                    self._send(200, boss_state(conn))
                finally:
                    conn.close()
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
                                              a.get("category", ""), a.get("stage", ""), quiet=True,
                                              dept=a.get("dept", "")),
                        "update": lambda: op_update(conn, a["code"], a.get("title"),
                                                    a.get("category"), a.get("stage"), a.get("status"),
                                                    a.get("dept")),
                        "sent": lambda: op_sent(conn, int(a["id"])),
                        "answer": lambda: op_answer(conn, int(a["id"]), a.get("answer", "")),
                        "wait": lambda: op_wait(conn, a["code"], a["party"], a["what"], a.get("who", ""),
                                                a.get("since"), a.get("clear", False), quiet=True,
                                                mailbox=a.get("mailbox", ""), search=a.get("search", ""),
                                                folder=a.get("mail_folder", ""), mail_to=a.get("mail_to", "")),
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
                    if req.get("view") == "boss":
                        self._send(200, boss_state(conn))
                    else:
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
    s.add_argument("-d", "--dept", default="", help="部门代码(见 depts)")
    s.set_defaults(f=lambda a: op_add(db(), a.code, a.title, a.category, a.stage, dept=a.dept))

    s = sub.add_parser("update", help="更新项目字段")
    s.add_argument("code"); s.add_argument("-t", "--title"); s.add_argument("-c", "--category")
    s.add_argument("-s", "--stage"); s.add_argument("--status", choices=["active", "closed"])
    s.add_argument("-d", "--dept", help="部门代码；空串清空")
    s.set_defaults(f=lambda a: op_update(db(), a.code, a.title, a.category, a.stage, a.status, a.dept))

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
    s.add_argument("--mail", default="", help="邮件所在邮箱(如 qt / tt)")
    s.add_argument("--search", default="", help="能搜到那封邮件的搜索串, 如 'subject:\"Re: xxx\"'")
    s.add_argument("--folder", default="", choices=MAIL_FOLDERS, help="inbox / drafts / sent")
    s.add_argument("--to", default="", help="草稿的实际收件人")
    s.set_defaults(f=lambda a: op_wait(db(), a.code, a.party, a.what, a.who, a.since, a.clear,
                                       mailbox=a.mail, search=a.search, folder=a.folder, mail_to=a.to))

    s = sub.add_parser("wait-mail", help="给已有等待项补/改邮件定位信息")
    s.add_argument("id", type=int)
    s.add_argument("--mail"); s.add_argument("--search")
    s.add_argument("--folder", choices=MAIL_FOLDERS); s.add_argument("--to")
    s.set_defaults(f=lambda a: op_wait_mail(db(), a.id, a.mail, a.search, a.folder, a.to))

    s = sub.add_parser("brief", help="设置/查看项目背景(2-3 行: 什么事/谁/要什么), 也可挂 session 链接")
    s.add_argument("code"); s.add_argument("text", nargs="?")
    s.add_argument("--session", help="项目 session 链接或 session id")
    s.set_defaults(f=lambda a: op_brief(db(), a.code, a.text, a.session))

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
    s.add_argument("--dept", help="只看某部门；'-' 看未分部门")
    s.set_defaults(f=cmd_list)

    s = sub.add_parser("depts", help="列出/设置部门表")
    s.add_argument("--set", help='"CODE=名称;CODE=名称"（整表替换）')
    s.set_defaults(f=cmd_depts)

    s = sub.add_parser("ask", help="提请决策(显示在 /boss)")
    s.add_argument("code"); s.add_argument("question")
    g = s.add_mutually_exclusive_group(required=True)
    g.add_argument("--options", help='选项 "A|B|C"')
    g.add_argument("--text", action="store_true", help="要文字意见")
    g.add_argument("--ack", action="store_true", help="只需知悉")
    s.add_argument("--by", default="", help="提问者(部门/session)")
    def _ask(a):
        kind = "choice" if a.options else ("text" if a.text else "ack")
        op_ask(db(), a.code, a.question, kind, (a.options or "").split("|"), a.by)
    s.set_defaults(f=_ask)

    s = sub.add_parser("decisions", help="列出决策")
    g = s.add_mutually_exclusive_group()
    g.add_argument("--pending", action="store_true", help="未答")
    g.add_argument("--answered-unconsumed", action="store_true", help="已答未转交")
    s.set_defaults(f=cmd_decisions)

    s = sub.add_parser("answer", help="答复决策(等同 /boss 上点按钮)")
    s.add_argument("id", type=int); s.add_argument("answer", nargs="?", default="")
    s.set_defaults(f=lambda a: op_answer(db(), a.id, a.answer))

    s = sub.add_parser("consume", help="标记决策答复已转交处理")
    s.add_argument("id", type=int)
    s.set_defaults(f=lambda a: op_consume(db(), a.id))

    s = sub.add_parser("review", help="记录落笔前审查结果")
    s.add_argument("code"); s.add_argument("--action", required=True, choices=REVIEW_ACTIONS)
    s.add_argument("--verdict", required=True, choices=["pass", "fail"])
    s.add_argument("--plan-file"); s.add_argument("--plan", default="")
    s.add_argument("--findings", default=""); s.add_argument("--reviewer", default="")
    def _review(a):
        plan = open(a.plan_file, encoding="utf-8").read() if a.plan_file else a.plan
        op_review(db(), a.code, a.action, a.verdict, plan, a.findings, a.reviewer)
    s.set_defaults(f=_review)

    s = sub.add_parser("review-ok", help="检查近期是否有通过的审查(退出码 0/1)")
    s.add_argument("code"); s.add_argument("--action", required=True, choices=REVIEW_ACTIONS)
    s.add_argument("--within", type=int, default=30, help="分钟")
    def _rok(a):
        ok, msg = review_ok(db(), a.code, a.action, a.within)
        print(("OK " if ok else "NO ") + msg)
        sys.exit(0 if ok else 1)
    s.set_defaults(f=_rok)

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
