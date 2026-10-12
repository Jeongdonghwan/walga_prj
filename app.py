# -*- coding: utf-8 -*-
"""왈가(walga) — Flask 라우트. Phase 1: 회원/유동닉, 게시판, 댓글, 추천·왈베, 신고·블라인드."""
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta
from functools import wraps
from zoneinfo import ZoneInfo

import pymysql
from flask import (Flask, Response, abort, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from flask_wtf.csrf import CSRFProtect
from markupsafe import Markup, escape
from PIL import Image
from werkzeug.security import check_password_hash, generate_password_hash

import models as m
from config import Config

KST = ZoneInfo("Asia/Seoul")
BOARDS = Config.BOARDS
SORTS = {"new": "p.id DESC", "best": "p.rec_cnt DESC, p.id DESC", "cmt": "p.cmt_cnt DESC, p.id DESC"}
NICK_RE = re.compile(r"^[가-힣A-Za-z0-9_]{2,12}$")
PW4_RE = re.compile(r"^\d{4}$")
POST_SQL = "SELECT p.*, COALESCE(u.nickname, p.anon_nick) nick FROM posts p LEFT JOIN users u ON u.id=p.user_id"

app = Flask(__name__)
app.config.from_object(Config)
app.teardown_appcontext(m.close_db)
CSRFProtect(app)


# ─── 공통 ───────────────────────────────────────────────

def now_kst():
    return datetime.now(KST).replace(tzinfo=None)


def client_ip():
    xff = request.headers.get("X-Forwarded-For", "")
    return (xff.split(",")[0].strip() if xff else request.remote_addr or "")[:45]


@app.before_request
def _anon_uid():
    g.uid = request.cookies.get("wuid") or uuid.uuid4().hex
    g.new_uid = "wuid" not in request.cookies


@app.after_request
def _set_uid(resp):
    if g.get("new_uid"):
        resp.set_cookie("wuid", g.uid, max_age=365 * 86400, samesite="Lax", httponly=True)
    return resp


def anon_hash():
    # ponytail: 쿠키+IP 해시(스펙). IP가 바뀌는 모바일은 다른 사람 취급 → 로그인 유도가 업그레이드 경로
    return hashlib.sha256(f"{app.secret_key}{g.uid}{client_ip()}".encode()).hexdigest()


def ident():
    """(user_id, anon_hash): 로그인은 user_id만, 비로그인은 anon_hash만 채움."""
    uid = session.get("uid")
    return (uid, None) if uid else (None, anon_hash())


def author_fields(form):
    """글·댓글 작성자 컬럼. 비로그인은 닉네임+4자리 비밀번호(유동닉)."""
    if session.get("uid"):
        return {"user_id": session["uid"], "anon_nick": None, "anon_pw_hash": None}
    nick, pw = form.get("nick", "").strip(), form.get("pw", "")
    if not NICK_RE.match(nick):
        raise ValueError("닉네임은 2~12자 (한글·영문·숫자)")
    if not PW4_RE.match(pw):
        raise ValueError("비밀번호는 숫자 4자리")
    if m.one("SELECT 1 FROM users WHERE nickname=%s", (nick,)):
        raise ValueError("회원 닉네임임. 다른 닉네임 쓰셈")
    return {"user_id": None, "anon_nick": nick, "anon_pw_hash": generate_password_hash(pw)}


def is_owner(row, pw):
    if row["user_id"]:
        return session.get("uid") == row["user_id"]
    return bool(pw) and check_password_hash(row["anon_pw_hash"], pw)


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **k):
        auth, cfg = request.authorization, app.config
        if not (auth and cfg["ADMIN_ID"] and auth.username == cfg["ADMIN_ID"] and auth.password == cfg["ADMIN_PW"]):
            return Response("", 401, {"WWW-Authenticate": 'Basic realm="walga admin"'})
        return view(*a, **k)
    return wrapped


def save_image(file, pid):
    """업로드 이미지 1장을 static/uploads/<pid>.jpg 로 리사이즈 저장. 성공 시 True."""
    if not file or not file.filename:
        return False
    try:
        img = Image.open(file.stream)
        img = img.convert("RGB")  # ponytail: GIF 애니 손실. 필요하면 확장자 컬럼 추가해 원본 보존
        img.thumbnail((app.config["IMG_MAX_SIDE"],) * 2)
        os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
        img.save(os.path.join(app.config["UPLOAD_DIR"], f"{pid}.jpg"), "JPEG", quality=85)
        return True
    except Exception:
        return False


@app.template_filter("nl2br")
def nl2br(s):
    return Markup("<br>").join(escape(s or "").split("\n"))


@app.template_filter("ftime")
def ftime(dt):
    if not dt:
        return ""
    return dt.strftime("%H:%M") if dt.date() == now_kst().date() else dt.strftime("%m.%d")


@app.template_filter("comma")
def comma(n):
    return f"{n or 0:,}"


@app.context_processor
def _ctx():
    return {"BOARDS": BOARDS, "nick": session.get("nick"), "AD_ENABLED": app.config["AD_ENABLED"]}


def paging(total):
    page = max(1, request.args.get("page", 1, type=int))
    per = app.config["PER_PAGE"]
    return page, (page - 1) * per, max(1, -(-total // per))


def safe_back(default):
    """같은 사이트의 referrer만 되돌아감."""
    r = request.referrer or ""
    return r if r.startswith(request.host_url) else default


# ─── 이슈 공통 ───────────────────────────────────────────

ISSUE_OPEN_COLS = "id, title, summary, category, is_main, opt_yes, opt_no, open_date, slug, closed, yes_cnt+no_cnt part"
ISSUE_CMT = "(SELECT COUNT(*) FROM comments c WHERE c.target='issue' AND c.target_id=issues.id) cmt"


def slugify(t):
    return re.sub(r"[^\w가-힣]+", "-", t).strip("-")[:80] or "issue"


def with_yp(rows):
    """마감 이슈 행에 참여수(part)·찬성 비율(yp)·다수파 라벨(maj) 추가."""
    for r in rows:
        part = r["yes_cnt"] + r["no_cnt"]
        r["part"] = part
        r["yp"] = round(r["yes_cnt"] * 100 / part) if part else 50
        r["maj"] = None if r["yes_cnt"] == r["no_cnt"] else (r["opt_yes"] if r["yes_cnt"] > r["no_cnt"] else r["opt_no"])
    return rows


def close_issue(i):
    """이슈 1건 마감 확정 + 다수파 투표자 포인트 지급. 동시 요청은 rowcount로 한 번만."""
    if m.affect("UPDATE issues SET closed=1 WHERE id=%s AND closed=0", (i["id"],)) != 1:
        return
    if i["yes_cnt"] != i["no_cnt"]:
        m.run("UPDATE users u JOIN votes v ON v.user_id=u.id SET u.points=u.points+%s WHERE v.issue_id=%s AND v.choice=%s",
              (app.config["POINTS_WIN"], i["id"], 1 if i["yes_cnt"] > i["no_cnt"] else 0))
    make_card(i["id"])


def close_due_issues():
    """지연 마감: open_date가 지난(=23:59 경과) 이슈를 요청 시점에 닫음. 타이머 없음."""
    for i in m.rows("SELECT id, yes_cnt, no_cnt FROM issues WHERE closed=0 AND open_date<%s", (now_kst().date(),)):
        close_issue(i)


def make_card(iid):
    """Phase 4: 공유용 결과 카드 이미지."""


def my_vote(iid):
    uid, ah = ident()
    v = m.one("SELECT choice FROM votes WHERE issue_id=%s AND (user_id=%s OR anon_hash=%s)", (iid, uid, ah))
    return v["choice"] if v else None


# ─── 홈 ─────────────────────────────────────────────────

@app.get("/")
def home():
    close_due_issues()
    today = now_kst().date()
    yday = today - timedelta(days=1)
    # 핵심 규칙 1: 진행 중 이슈는 참여자 수만 — yes_cnt/no_cnt 자체를 템플릿에 넘기지 않음
    main_issue = m.one(f"SELECT {ISSUE_OPEN_COLS} FROM issues WHERE open_date=%s AND is_main=1 AND closed=0 AND approved=1 LIMIT 1", (today,))
    issues = m.rows(f"SELECT {ISSUE_OPEN_COLS}, {ISSUE_CMT} FROM issues WHERE open_date=%s AND is_main=0 AND closed=0 AND approved=1 "
                    "ORDER BY id LIMIT 5", (today,))
    results = with_yp(m.rows("SELECT * FROM issues WHERE closed=1 AND open_date=%s AND approved=1 "
                             "ORDER BY is_main DESC, yes_cnt+no_cnt DESC LIMIT 3", (yday,)))
    main_voted = my_vote(main_issue["id"]) if main_issue else None
    best = m.rows(f"{POST_SQL} WHERE p.is_best=1 AND p.status=1 ORDER BY p.best_at DESC, p.id DESC LIMIT 5")
    free = m.rows(f"{POST_SQL} WHERE p.board='free' AND p.status=1 ORDER BY p.id DESC LIMIT 5")
    sort = request.args.get("sort", "new")
    total = m.one("SELECT COUNT(*) n FROM posts WHERE status=1")["n"]
    page, off, pages = paging(total)
    posts = m.rows(f"{POST_SQL} WHERE p.status=1 ORDER BY {SORTS.get(sort, SORTS['new'])} LIMIT %s OFFSET %s",
                   (app.config["PER_PAGE"], off))
    user = m.one("SELECT *, DATEDIFF(CURDATE(), created_at)+1 days FROM users WHERE id=%s", (session["uid"],)) if session.get("uid") else None
    return render_template("home.html", main_issue=main_issue, main_voted=main_voted, issues=issues, results=results,
                           best=best, free=free, posts=posts, sort=sort, page=page, pages=pages, user=user,
                           today_label=f"{today.month}월 {today.day}일", y_label=f"{yday.month}월 {yday.day}일")


# ─── 이슈 · 투표 ─────────────────────────────────────────

@app.get("/issue")
def issue_list():
    close_due_issues()
    today = now_kst().date()
    yday = today - timedelta(days=1)
    open_issues = m.rows(f"SELECT {ISSUE_OPEN_COLS}, {ISSUE_CMT} FROM issues WHERE open_date=%s AND closed=0 AND approved=1 "
                         "ORDER BY is_main DESC, id", (today,))
    yres = with_yp(m.rows(f"SELECT *, {ISSUE_CMT} FROM issues WHERE closed=1 AND open_date=%s AND approved=1 "
                          "ORDER BY is_main DESC, yes_cnt+no_cnt DESC", (yday,)))
    total = m.one("SELECT COUNT(*) n FROM issues WHERE closed=1 AND open_date<%s AND approved=1", (yday,))["n"]
    page, off, pages = paging(total)
    archive = with_yp(m.rows("SELECT * FROM issues WHERE closed=1 AND open_date<%s AND approved=1 "
                             "ORDER BY open_date DESC, is_main DESC, id DESC LIMIT %s OFFSET %s", (yday, app.config["PER_PAGE"], off)))
    return render_template("issues.html", open_issues=open_issues, yres=yres, archive=archive, page=page, pages=pages,
                           today_label=f"{today.month}월 {today.day}일", y_label=f"{yday.month}월 {yday.day}일")


@app.get("/issue/<int:iid>")
@app.get("/issue/<int:iid>/<slug>")
def issue(iid, slug=None):
    close_due_issues()
    i = m.one("SELECT * FROM issues WHERE id=%s AND approved=1", (iid,))
    if not i:
        abort(404)
    if i["slug"] and slug != i["slug"]:
        return redirect(url_for("issue", iid=iid, slug=i["slug"]), 301)
    m.run("UPDATE issues SET view_cnt=view_cnt+1 WHERE id=%s", (iid,))
    if i["closed"]:
        with_yp([i])
    else:
        i["part"] = i["yes_cnt"] + i["no_cnt"]
        i["yes_cnt"] = i["no_cnt"] = None  # 핵심 규칙 1: 진행 중엔 집계 비공개
    mine = my_vote(iid)
    comments = load_comments("issue", iid)
    cmt_n = m.one("SELECT COUNT(*) n FROM comments WHERE target='issue' AND target_id=%s", (iid,))["n"]
    return render_template("issue.html", i=i, mine=mine, comments=comments, cmt_n=cmt_n, can_comment=mine is not None)


@app.post("/vote")
def vote():
    close_due_issues()
    iid, choice = request.form.get("issue_id", type=int), request.form.get("choice", type=int)
    if choice not in (0, 1):
        abort(400)
    i = m.one("SELECT id, closed, slug FROM issues WHERE id=%s AND approved=1", (iid,))
    if not i:
        abort(404)
    back = safe_back(url_for("issue", iid=iid, slug=i["slug"]))
    if i["closed"]:
        flash("마감된 이슈임")
        return redirect(back)
    uid, ah = ident()
    try:
        m.run("INSERT INTO votes(issue_id, user_id, anon_hash, choice) VALUES(%s,%s,%s,%s)", (iid, uid, ah, choice))
    except pymysql.err.IntegrityError:
        flash("이미 투표함")
        return redirect(back)
    col = "yes_cnt" if choice else "no_cnt"
    m.run(f"UPDATE issues SET {col}={col}+1 WHERE id=%s", (iid,))
    if uid:  # 스트릭·출석: 어제 투표했으면 +1, 오늘 이미 했으면 유지, 아니면 1
        today = now_kst().date()
        m.run("INSERT IGNORE INTO attendance(user_id, date) VALUES(%s,%s)", (uid, today))
        m.run("UPDATE users SET streak=CASE WHEN last_vote_date=%s THEN streak WHEN last_vote_date=%s THEN streak+1 ELSE 1 END, "
              "last_vote_date=%s WHERE id=%s", (today, today - timedelta(days=1), today, uid))
    flash("투표 완료. 결과는 밤 11시 59분에 공개됨")
    return redirect(back)


# ─── 회원 ───────────────────────────────────────────────

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        nick, pw = request.form.get("nickname", "").strip(), request.form.get("password", "")
        if not NICK_RE.match(nick):
            flash("닉네임은 2~12자 (한글·영문·숫자)")
        elif len(pw) < 4:
            flash("비밀번호는 4자 이상")
        elif m.one("SELECT 1 FROM users WHERE nickname=%s", (nick,)):
            flash("이미 있는 닉네임")
        else:
            uid = m.run("INSERT INTO users(nickname, pw_hash) VALUES(%s,%s)", (nick, generate_password_hash(pw)))
            session.update(uid=uid, nick=nick)
            return redirect(url_for("home"))
    return render_template("auth.html", mode="signup")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = m.one("SELECT * FROM users WHERE nickname=%s", (request.form.get("nickname", "").strip(),))
        if u and check_password_hash(u["pw_hash"], request.form.get("password", "")):
            session.update(uid=u["id"], nick=u["nickname"])
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("home"))
        flash("닉네임 또는 비밀번호가 틀림")
    return render_template("auth.html", mode="login")


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


# ─── 게시판 ─────────────────────────────────────────────

@app.get("/board/<board>")
def board(board):
    if board not in BOARDS:
        abort(404)
    sort = request.args.get("sort", "new")
    total = m.one("SELECT COUNT(*) n FROM posts WHERE board=%s AND status=1", (board,))["n"]
    page, off, pages = paging(total)
    posts = m.rows(f"{POST_SQL} WHERE p.board=%s AND p.status=1 ORDER BY {SORTS.get(sort, SORTS['new'])} LIMIT %s OFFSET %s",
                   (board, app.config["PER_PAGE"], off))
    return render_template("board.html", board=board, title=BOARDS[board], posts=posts,
                           sort=sort, page=page, pages=pages)


@app.get("/best")
def best():
    total = m.one("SELECT COUNT(*) n FROM posts WHERE is_best=1 AND status=1")["n"]
    page, off, pages = paging(total)
    posts = m.rows(f"{POST_SQL} WHERE p.is_best=1 AND p.status=1 ORDER BY p.best_at DESC, p.id DESC LIMIT %s OFFSET %s",
                   (app.config["PER_PAGE"], off))
    return render_template("board.html", board=None, title="왈가베스트", posts=posts,
                           sort=None, page=page, pages=pages,
                           notice=f"추천 {app.config['BEST_THRESHOLD']}개를 받으면 왈베로 자동 승격됨")


@app.get("/post/<int:pid>")
def post(pid):
    p = m.one(f"{POST_SQL} WHERE p.id=%s", (pid,))
    if not p:
        abort(404)
    if p["status"] == 0:
        return render_template("post.html", p=p, comments=[], blind=True, can_edit=False)
    m.run("UPDATE posts SET view_cnt=view_cnt+1 WHERE id=%s", (pid,))
    return render_template("post.html", p=p, comments=load_comments("post", pid), blind=False,
                           can_edit=p["user_id"] is None or p["user_id"] == session.get("uid"))


def load_comments(target, tid):
    """1단계 대댓글 트리. 부모가 없어진 답글은 루트로 올림."""
    cs = m.rows("SELECT c.*, COALESCE(u.nickname, c.anon_nick) nick FROM comments c LEFT JOIN users u ON u.id=c.user_id "
                "WHERE c.target=%s AND c.target_id=%s ORDER BY c.id", (target, tid))
    by_id, roots = {}, []
    for c in cs:
        c["replies"] = []
        by_id[c["id"]] = c
        (by_id[c["parent_id"]]["replies"] if c["parent_id"] in by_id else roots).append(c)
    return roots


@app.route("/write", methods=["GET", "POST"])
def write():
    board = request.values.get("board", "free")
    if board not in BOARDS:
        abort(404)
    if request.method == "POST":
        title, content = request.form.get("title", "").strip()[:120], request.form.get("content", "").strip()
        try:
            if not title or not content:
                raise ValueError("제목과 내용은 필수")
            a = author_fields(request.form)
        except ValueError as e:
            flash(str(e))
            return render_template("write.html", board=board, p=request.form, edit=False)
        pid = m.run("INSERT INTO posts(board,user_id,anon_nick,anon_pw_hash,title,content) VALUES(%s,%s,%s,%s,%s,%s)",
                    (board, a["user_id"], a["anon_nick"], a["anon_pw_hash"], title, content))
        if save_image(request.files.get("image"), pid):
            m.run("UPDATE posts SET has_image=1 WHERE id=%s", (pid,))
        return redirect(url_for("post", pid=pid))
    return render_template("write.html", board=board, p=None, edit=False)


@app.route("/post/<int:pid>/edit", methods=["GET", "POST"])
def post_edit(pid):
    p = m.one("SELECT * FROM posts WHERE id=%s AND status=1", (pid,))
    if not p:
        abort(404)
    if p["user_id"] and p["user_id"] != session.get("uid"):
        abort(403)
    if request.method == "POST":
        title, content = request.form.get("title", "").strip()[:120], request.form.get("content", "").strip()
        if not is_owner(p, request.form.get("pw", "")):
            flash("비밀번호가 틀림")
        elif not title or not content:
            flash("제목과 내용은 필수")
        else:
            m.run("UPDATE posts SET title=%s, content=%s WHERE id=%s", (title, content, pid))
            if save_image(request.files.get("image"), pid):
                m.run("UPDATE posts SET has_image=1 WHERE id=%s", (pid,))
            return redirect(url_for("post", pid=pid))
    return render_template("write.html", board=p["board"], p=p, edit=True)


@app.post("/post/<int:pid>/delete")
def post_delete(pid):
    p = m.one("SELECT * FROM posts WHERE id=%s", (pid,))
    if not p:
        abort(404)
    if not is_owner(p, request.form.get("pw", "")):
        return jsonify(ok=False, msg="비밀번호가 틀림"), 403
    m.run("DELETE FROM comments WHERE target='post' AND target_id=%s", (pid,))
    m.run("DELETE FROM posts WHERE id=%s", (pid,))
    return jsonify(ok=True, to=url_for("board", board=p["board"]))


# ─── 댓글 ───────────────────────────────────────────────

@app.post("/comment")
def comment():
    target, tid = request.form.get("target"), request.form.get("target_id", type=int)
    parent = request.form.get("parent_id", type=int) or None
    content = request.form.get("content", "").strip()[:2000]
    if target == "post":
        if not m.one("SELECT 1 FROM posts WHERE id=%s AND status=1", (tid,)):
            abort(404)
        back = url_for("post", pid=tid)
    elif target == "issue":  # 핵심 규칙 2: 투표자만 댓글
        if not m.one("SELECT 1 FROM issues WHERE id=%s AND approved=1", (tid,)):
            abort(404)
        if my_vote(tid) is None:
            abort(403)
        back = url_for("issue", iid=tid)
    else:
        abort(400)
    try:
        if not content:
            raise ValueError("내용을 입력하셈")
        a = author_fields(request.form)
    except ValueError as e:
        flash(str(e))
        return redirect(back)
    if parent and not m.one("SELECT 1 FROM comments WHERE id=%s AND target=%s AND target_id=%s", (parent, target, tid)):
        parent = None
    m.run("INSERT INTO comments(target,target_id,parent_id,user_id,anon_nick,anon_pw_hash,content) VALUES(%s,%s,%s,%s,%s,%s,%s)",
          (target, tid, parent, a["user_id"], a["anon_nick"], a["anon_pw_hash"], content))
    if target == "post":
        m.run("UPDATE posts SET cmt_cnt=cmt_cnt+1 WHERE id=%s", (tid,))
    return redirect(back)


@app.post("/comment/<int:cid>/delete")
def comment_delete(cid):
    c = m.one("SELECT * FROM comments WHERE id=%s", (cid,))
    if not c:
        abort(404)
    if not is_owner(c, request.form.get("pw", "")):
        return jsonify(ok=False, msg="비밀번호가 틀림"), 403
    m.run("DELETE FROM comments WHERE id=%s", (cid,))
    if c["target"] == "post":
        m.run("UPDATE posts SET cmt_cnt=GREATEST(cmt_cnt-1,0) WHERE id=%s", (c["target_id"],))
    return jsonify(ok=True)


# ─── 추천 / 신고 ─────────────────────────────────────────

@app.post("/rec")
def rec():
    target, tid = request.form.get("target"), request.form.get("target_id", type=int)
    if target not in ("post", "comment") or not tid:
        abort(400)
    uid, ah = ident()
    try:
        m.run("INSERT INTO recommends(target,target_id,user_id,anon_hash) VALUES(%s,%s,%s,%s)", (target, tid, uid, ah))
    except pymysql.err.IntegrityError:
        return jsonify(ok=False, msg="이미 추천함"), 409
    if target == "post":
        th = app.config["BEST_THRESHOLD"]  # 핵심 규칙 3: SET은 좌→우 평가라 rec_cnt는 증가 후 값
        m.run("UPDATE posts SET rec_cnt=rec_cnt+1, best_at=IF(is_best=0 AND rec_cnt>=%s, NOW(), best_at), "
              "is_best=IF(rec_cnt>=%s, 1, is_best) WHERE id=%s", (th, th, tid))
        row = m.one("SELECT rec_cnt FROM posts WHERE id=%s", (tid,))
    else:
        m.run("UPDATE comments SET rec_cnt=rec_cnt+1 WHERE id=%s", (tid,))
        row = m.one("SELECT rec_cnt FROM comments WHERE id=%s", (tid,))
    return jsonify(ok=True, rec_cnt=row["rec_cnt"] if row else 0)


@app.post("/report")
def report():
    target, tid = request.form.get("target"), request.form.get("target_id", type=int)
    if target not in ("post", "comment") or not tid:
        abort(400)
    uid, ah = ident()
    rh = f"u{uid}" if uid else ah
    try:
        m.run("INSERT INTO reports(target,target_id,reason,reporter_hash) VALUES(%s,%s,%s,%s)",
              (target, tid, request.form.get("reason", "")[:50], rh))
    except pymysql.err.IntegrityError:
        return jsonify(ok=False, msg="이미 신고함"), 409
    tbl = "posts" if target == "post" else "comments"
    m.run(f"UPDATE {tbl} SET status=0 WHERE id=%s AND (SELECT COUNT(*) FROM reports WHERE target=%s AND target_id=%s)>=%s",
          (tid, target, tid, app.config["REPORT_BLIND"]))
    return jsonify(ok=True)


# ─── 어드민 ─────────────────────────────────────────────

@app.route("/admin", methods=["GET"])
@admin_required
def admin():
    return redirect(url_for("admin_issues"))


@app.route("/admin/issues", methods=["GET", "POST"])
@admin_required
def admin_issues():
    if request.method == "POST":
        f = request.form
        iid = f.get("id", type=int)
        title = f.get("title", "").strip()[:200]
        if not title:
            flash("제목은 필수")
            return redirect(url_for("admin_issues"))
        vals = (title, f.get("summary", "").strip(), f.get("category", "").strip()[:20] or None,
                1 if f.get("is_main") else 0, f.get("opt_yes", "").strip()[:30] or "찬성", f.get("opt_no", "").strip()[:30] or "반대",
                f.get("open_date") or now_kst().date(), 1 if f.get("approved") else 0, slugify(title))
        if iid:
            m.run("UPDATE issues SET title=%s, summary=%s, category=%s, is_main=%s, opt_yes=%s, opt_no=%s, open_date=%s, approved=%s, slug=%s "
                  "WHERE id=%s", vals + (iid,))
        else:
            iid = m.run("INSERT INTO issues(title, summary, category, is_main, opt_yes, opt_no, open_date, approved, slug) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", vals)
        if vals[3]:  # 오늘의 왈가는 하루 1개
            m.run("UPDATE issues SET is_main=0 WHERE open_date=%s AND id<>%s", (vals[6], iid))
        return redirect(url_for("admin_issues"))
    edit_id = request.args.get("edit", type=int)
    edit = m.one("SELECT * FROM issues WHERE id=%s", (edit_id,)) if edit_id else None
    issues = m.rows("SELECT *, yes_cnt+no_cnt part FROM issues ORDER BY open_date DESC, is_main DESC, id DESC LIMIT 60")
    return render_template("admin/issues.html", issues=issues, edit=edit, today=now_kst().date())


@app.post("/admin/issues/<int:iid>/<action>")
@admin_required
def admin_issue_action(iid, action):
    i = m.one("SELECT * FROM issues WHERE id=%s", (iid,))
    if not i:
        abort(404)
    if action == "delete":
        m.run("DELETE FROM votes WHERE issue_id=%s", (iid,))
        m.run("DELETE FROM comments WHERE target='issue' AND target_id=%s", (iid,))
        m.run("DELETE FROM issues WHERE id=%s", (iid,))
    elif action == "approve":
        m.run("UPDATE issues SET approved=1 WHERE id=%s", (iid,))
    elif action == "main":
        m.run("UPDATE issues SET is_main=IF(id=%s, 1, 0) WHERE open_date=%s", (iid, i["open_date"]))
    elif action == "close":
        close_issue(i)
    elif action == "reopen":
        m.run("UPDATE issues SET closed=0 WHERE id=%s", (iid,))
    return redirect(url_for("admin_issues"))

@app.route("/admin/blind", methods=["GET", "POST"])
@admin_required
def admin_blind():
    if request.method == "POST":
        target, tid = request.form.get("target"), request.form.get("target_id", type=int)
        status = request.form.get("status", type=int)
        if target in ("post", "comment") and status in (0, 1):
            tbl = "posts" if target == "post" else "comments"
            m.run(f"UPDATE {tbl} SET status=%s WHERE id=%s", (status, tid))
        return redirect(url_for("admin_blind"))
    items = m.rows(
        "SELECT r.target, r.target_id, COUNT(*) n, GROUP_CONCAT(r.reason SEPARATOR ' / ') reasons, "
        "COALESCE(p.title, c.content) txt, COALESCE(p.status, c.status) status "
        "FROM reports r LEFT JOIN posts p ON r.target='post' AND p.id=r.target_id "
        "LEFT JOIN comments c ON r.target='comment' AND c.id=r.target_id "
        "GROUP BY r.target, r.target_id, p.title, c.content, p.status, c.status ORDER BY status ASC, n DESC LIMIT 200")
    return render_template("admin/blind.html", items=items)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5050)))
