# -*- coding: utf-8 -*-
"""왈가(walga) — Flask 라우트. Phase 1: 회원/유동닉, 게시판, 댓글, 추천·왈베, 신고·블라인드."""
import hashlib
import os
import re
import uuid
from datetime import datetime
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
BOARDS = {"free": "자유", "humor": "유머"}
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


@app.context_processor
def _ctx():
    return {"BOARDS": BOARDS, "nick": session.get("nick"), "AD_ENABLED": app.config["AD_ENABLED"]}


def paging(total):
    page = max(1, request.args.get("page", 1, type=int))
    per = app.config["PER_PAGE"]
    return page, (page - 1) * per, max(1, -(-total // per))


# ─── 홈 (Phase 3 전까지 임시) ──────────────────────────────

@app.get("/")
def home():
    return redirect(url_for("board", board="free"))


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
        uid, ah = ident()
        if not m.one("SELECT 1 FROM votes WHERE issue_id=%s AND (user_id=%s OR anon_hash=%s)", (tid, uid, ah)):
            abort(403)
        back = request.referrer or url_for("home")
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
    app.run(debug=True, port=5000)
