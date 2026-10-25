# -*- coding: utf-8 -*-
"""왈가(walga) — Flask 라우트. Phase 1: 회원/유동닉, 게시판, 댓글, 추천·왈베, 신고·블라인드."""
import hashlib
import os
import re
import uuid
from datetime import datetime, timedelta
from functools import wraps
try:
    from zoneinfo import ZoneInfo
except ImportError:  # 서버 python 3.8 대비
    from backports.zoneinfo import ZoneInfo

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
POST_SQL = "SELECT p.*, COALESCE(u.nickname, p.anon_nick) nick, u.points upts FROM posts p LEFT JOIN users u ON u.id=p.user_id"

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


def login_required(view):
    @wraps(view)
    def wrapped(*a, **k):
        if not session.get("uid"):
            return redirect(url_for("login", next=request.path))
        return view(*a, **k)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*a, **k):
        auth, cfg = request.authorization, app.config
        if not (auth and cfg["ADMIN_ID"] and auth.username == cfg["ADMIN_ID"] and auth.password == cfg["ADMIN_PW"]):
            return Response("", 401, {"WWW-Authenticate": 'Basic realm="walga admin"'})
        return view(*a, **k)
    return wrapped


def save_image(file, name):
    """업로드 이미지 1장을 static/uploads/<name>.jpg 로 리사이즈 저장. 성공 시 True. (글=<id>, 모임=m<id>)"""
    if not file or not file.filename:
        return False
    try:
        img = Image.open(file.stream)
        img = img.convert("RGB")  # ponytail: GIF 애니 손실. 필요하면 확장자 컬럼 추가해 원본 보존
        img.thumbnail((app.config["IMG_MAX_SIDE"],) * 2)
        os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
        img.save(os.path.join(app.config["UPLOAD_DIR"], f"{name}.jpg"), "JPEG", quality=85)
        return True
    except Exception:
        return False


WEEKDAYS = "월화수목금토일"


@app.template_filter("fdt")
def fdt(dt):
    """모임 일시: 10/28 (화) 19:30"""
    return f"{dt.month}/{dt.day} ({WEEKDAYS[dt.weekday()]}) {dt:%H:%M}" if dt else ""


@app.template_filter("dday")
def dday(dt):
    d = (dt.date() - now_kst().date()).days
    return "D-DAY" if d == 0 else (f"D-{d}" if d > 0 else "종료")


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


@app.template_filter("lv")
def lv(points):
    """누적 포인트 → 레벨 번호. None(유동닉)은 0."""
    return sum(1 for t in app.config["LEVELS"] if (points or 0) >= t) if points is not None else 0


@app.template_filter("avc")
def avc(nick):
    """닉네임 → 아바타 색상 클래스 번호(0~7). 같은 닉은 항상 같은 색."""
    return sum(ord(c) for c in (nick or "")) % 8


def next_level_gap(points):
    for t in app.config["LEVELS"]:
        if points < t:
            return t - points
    return 0


@app.context_processor
def _ctx():
    return {"BOARDS": BOARDS, "nick": session.get("nick"), "AD_ENABLED": app.config["AD_ENABLED"], "side_data": side_data}


def side_best():
    return [{"url": f"/post/{p['id']}", "title": p["title"], "img": f"/static/uploads/{p['id']}.jpg" if p["has_image"] else None,
             "sub": p["cmt_cnt"] or None}
            for p in m.rows("SELECT id, title, has_image, cmt_cnt FROM posts WHERE is_best=1 AND status=1 ORDER BY best_at DESC, id DESC LIMIT 24")]


def side_data():
    """사이드바 위젯 데이터. 템플릿이 호출할 때만 실행 (1단 페이지는 비용 없음)."""
    today = now_kst().date()
    user = m.one("SELECT *, DATEDIFF(CURDATE(), created_at)+1 days FROM users WHERE id=%s", (session["uid"],)) if session.get("uid") else None
    main = m.one(f"SELECT {ISSUE_OPEN_COLS} FROM issues WHERE open_date=%s AND is_main=1 AND closed=0 AND approved=1 LIMIT 1", (today,))
    issues = m.rows(f"SELECT {ISSUE_OPEN_COLS} FROM issues WHERE open_date=%s AND is_main=0 AND closed=0 AND approved=1 ORDER BY id LIMIT 5", (today,))
    best = side_best()
    news = [{"url": n["url"], "title": n["title"], "img": n["image"], "sub": n["source"], "ext": True}
            for n in m.rows("SELECT url, title, image, source FROM news ORDER BY pinned DESC, id DESC LIMIT 16")]
    return {"user": user, "main": main, "main_voted": my_vote(main["id"]) if main else None, "issues": issues, "best": best, "news": news}


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


CARD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "cards")
FONT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts", "Pretendard-Bold.otf")


def make_card(iid):
    """공유용 결과 카드 1200×630 → static/cards/<id>.png. 실패해도 서비스는 계속."""
    try:
        from PIL import ImageDraw, ImageFont
        i = m.one("SELECT * FROM issues WHERE id=%s", (iid,))
        if not i:
            return
        with_yp([i])
        img = Image.new("RGB", (1200, 630), "#ffffff")
        d = ImageDraw.Draw(img)
        f_lab, f_t, f_bar, f_m = (ImageFont.truetype(FONT, s) for s in (26, 54, 30, 26))
        d.rounded_rectangle((60, 60, 300, 108), 14, fill="#0e7c6f")
        d.text((80, 70), "왈가 · 오늘의 왈가" if i["is_main"] else "왈가 · 이슈", font=f_lab, fill="#ffffff")
        y = 150  # 제목 줄바꿈: 폭 기준 greedy
        line = ""
        for ch in i["title"]:
            if d.textlength(line + ch, font=f_t) > 1080:
                d.text((60, y), line, font=f_t, fill="#191f28")
                y += 70
                line = ch
            else:
                line += ch
            if y > 300:
                break
        d.text((60, y), line, font=f_t, fill="#191f28")
        yw = max(140, int(1080 * i["yp"] / 100))
        d.rounded_rectangle((60, 400, 1140, 470), 16, fill="#e9ebee")
        d.rounded_rectangle((60, 400, 60 + yw, 470), 16, fill="#0e7c6f")
        d.text((84, 416), f"{i['opt_yes']} {i['yp']}%", font=f_bar, fill="#ffffff")
        no = f"{i['opt_no']} {100 - i['yp']}%"
        d.text((1116 - d.textlength(no, font=f_bar), 416), no, font=f_bar, fill="#6b7684")
        d.text((60, 530), f"{i['part']:,}명 참여 · {i['open_date'].month}월 {i['open_date'].day}일 마감", font=f_m, fill="#9aa3ad")
        tail = "다음 판: 왈가에서"
        d.text((1140 - d.textlength(tail, font=f_m), 530), tail, font=f_m, fill="#0e7c6f")
        os.makedirs(CARD_DIR, exist_ok=True)
        img.save(os.path.join(CARD_DIR, f"{iid}.png"))
    except Exception as e:  # 카드 실패는 마감을 막지 않음
        app.logger.warning("card %s: %s", iid, e)


def card_url(iid):
    return url_for("static", filename=f"cards/{iid}.png", _external=True) if os.path.exists(os.path.join(CARD_DIR, f"{iid}.png")) else None


def gen_issues_today():
    """RSS → Claude → 오늘 날짜 이슈 6개 insert. 이미 있으면 건너뜀."""
    import issue_gen
    today = now_kst().date()
    if m.one("SELECT 1 FROM issues WHERE open_date=%s LIMIT 1", (today,)):
        return "오늘 날짜 이슈가 이미 있어서 건너뜀"
    main, issues = issue_gen.generate(app.config["ANTHROPIC_API_KEY"])
    ap = 1 if app.config["AUTO_PUBLISH"] else 0
    for k, i in enumerate([main] + issues):
        m.run("INSERT INTO issues(title, summary, category, is_main, opt_yes, opt_no, open_date, approved, slug) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
              (i["title"], i["summary"], i["category"], 1 if k == 0 else 0, i["opt_yes"], i["opt_no"], today, ap, slugify(i["title"])))
    return f"이슈 {1 + len(issues)}개 생성 ({'바로 게시됨' if ap else '승인 대기'})"


@app.cli.command("gen-issues")
def cli_gen_issues():
    """cron용: 30 7 * * * cd /var/www/walga && ./venv/bin/flask --app app gen-issues"""
    print(gen_issues_today())


def fetch_news_now(limit=24):
    """RSS 기사를 news 테이블에 적재. URL 중복은 DB UNIQ로 건너뜀. 새 글 중 이미지 없는 건 og:image 1회 조회."""
    import issue_gen
    n = 0
    for it in issue_gen.fetch_news(limit):
        if not m.affect("INSERT IGNORE INTO news(title, url, source, image) VALUES(%s,%s,%s,%s)", (it["title"], it["url"], it["source"], it["image"])):
            continue
        n += 1
        if not it["image"]:
            img = issue_gen.og_image(it["url"])
            if img:
                m.run("UPDATE news SET image=%s WHERE url=%s", (img, it["url"]))
    return f"뉴스 {n}건 추가"


@app.cli.command("fetch-news")
def cli_fetch_news():
    """cron용: 0 */3 * * * cd /var/www/walga && ./venv/bin/flask --app app fetch-news"""
    print(fetch_news_now())


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
    news = m.rows("SELECT * FROM news ORDER BY pinned DESC, id DESC LIMIT 8")
    sort = request.args.get("sort", "new")
    total = m.one("SELECT COUNT(*) n FROM posts WHERE status=1")["n"]
    page, off, pages = paging(total)
    posts = m.rows(f"{POST_SQL} WHERE p.status=1 ORDER BY {SORTS.get(sort, SORTS['new'])} LIMIT %s OFFSET %s",
                   (app.config["PER_PAGE"], off))
    user = m.one("SELECT *, DATEDIFF(CURDATE(), created_at)+1 days FROM users WHERE id=%s", (session["uid"],)) if session.get("uid") else None
    return render_template("home.html", main_issue=main_issue, main_voted=main_voted, issues=issues, results=results,
                           best=best, free=free, news=news, posts=posts, sort=sort, page=page, pages=pages, user=user,
                           side_best=side_best(),
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
    card = None
    if i["closed"]:
        if not card_url(iid):
            make_card(iid)
        card = card_url(iid)
    resp = app.make_response(render_template("issue.html", i=i, mine=mine, comments=comments, cmt_n=cmt_n,
                                             can_comment=mine is not None, card=card))
    if i["closed"] and mine is None and not session.get("uid"):
        # 핵심 규칙 6: 마감 이슈는 SEO 자산. 비로그인·미투표 방문자(크롤러 포함)에겐 캐시 허용
        # ponytail: nginx proxy_cache를 켜면 이 헤더로 10분 캐시됨. 파일 캐시는 세션별 CSRF 토큰 문제로 보류
        resp.headers["Cache-Control"] = "public, max-age=600"
    return resp


@app.get("/sitemap.xml")
def sitemap():
    base = (app.config["SITE_URL"] or request.host_url).rstrip("/")
    urls = [f"{base}/", f"{base}/issue", f"{base}/best"] + [f"{base}/board/{b}" for b in BOARDS]
    urls += [f"{base}/issue/{r['id']}/{r['slug'] or ''}" for r in m.rows("SELECT id, slug FROM issues WHERE approved=1 ORDER BY id DESC LIMIT 2000")]
    urls += [f"{base}/post/{r['id']}" for r in m.rows("SELECT id FROM posts WHERE status=1 ORDER BY id DESC LIMIT 2000")]
    urls += [f"{base}/meetup/{r['id']}" for r in m.rows("SELECT id FROM meetups WHERE status=1 ORDER BY id DESC LIMIT 500")]
    xml = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + \
          "".join(f"<url><loc>{escape(u)}</loc></url>" for u in urls) + "</urlset>"
    return Response(xml, mimetype="application/xml")


@app.get("/robots.txt")
def robots():
    base = (app.config["SITE_URL"] or request.host_url).rstrip("/")
    return Response(f"User-agent: *\nDisallow: /admin\nDisallow: /write\nSitemap: {base}/sitemap.xml\n", mimetype="text/plain")


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


# ─── 뉴스 ───────────────────────────────────────────────

@app.get("/news")
def news_list():
    total = m.one("SELECT COUNT(*) n FROM news")["n"]
    page, off, pages = paging(total)
    items = m.rows("SELECT * FROM news ORDER BY pinned DESC, id DESC LIMIT %s OFFSET %s", (app.config["PER_PAGE"], off))
    return render_template("news.html", items=items, page=page, pages=pages)


# ─── 마이 · 검색 ─────────────────────────────────────────

@app.get("/my")
@login_required
def my():
    close_due_issues()
    uid = session["uid"]
    u = m.one("SELECT *, DATEDIFF(CURDATE(), created_at)+1 days FROM users WHERE id=%s", (uid,))
    if not u:
        session.clear()
        return redirect(url_for("login"))
    st = m.one("SELECT COUNT(*) total, "
               "SUM(i.closed=1 AND i.yes_cnt<>i.no_cnt) decided, "
               "SUM(i.closed=1 AND i.yes_cnt<>i.no_cnt AND (i.yes_cnt>i.no_cnt)=(v.choice=1)) hits "
               "FROM votes v JOIN issues i ON i.id=v.issue_id WHERE v.user_id=%s", (uid,))
    votes = m.rows("SELECT v.choice, i.* FROM votes v JOIN issues i ON i.id=v.issue_id WHERE v.user_id=%s ORDER BY v.id DESC LIMIT 30", (uid,))
    for v in votes:
        if v["closed"]:
            with_yp([v])
            v["st"] = "동률" if not v["maj"] else ("적중" if (v["maj"] == v["opt_yes"]) == (v["choice"] == 1) else "빗나감")
        else:
            v["st"] = "진행 중"
    posts = m.rows(f"{POST_SQL} WHERE p.user_id=%s AND p.status=1 ORDER BY p.id DESC LIMIT 10", (uid,))
    cmts = m.rows("SELECT c.*, COALESCE(p.title, i.title) ttl FROM comments c "
                  "LEFT JOIN posts p ON c.target='post' AND p.id=c.target_id "
                  "LEFT JOIN issues i ON c.target='issue' AND i.id=c.target_id "
                  "WHERE c.user_id=%s ORDER BY c.id DESC LIMIT 10", (uid,))
    hit_rate = round(int(st["hits"] or 0) * 100 / int(st["decided"])) if st["decided"] else None
    return render_template("my.html", u=u, st=st, hit_rate=hit_rate, votes=votes, posts=posts, cmts=cmts,
                           gap=next_level_gap(u["points"]))


@app.post("/my/password")
@login_required
def my_password():
    u = m.one("SELECT pw_hash FROM users WHERE id=%s", (session["uid"],))
    new = request.form.get("new", "")
    if not check_password_hash(u["pw_hash"], request.form.get("cur", "")):
        flash("현재 비밀번호가 틀림")
    elif len(new) < 4:
        flash("새 비밀번호는 4자 이상")
    else:
        m.run("UPDATE users SET pw_hash=%s WHERE id=%s", (generate_password_hash(new), session["uid"]))
        flash("비밀번호 변경됨")
    return redirect(url_for("my"))


@app.get("/search")
def search():
    q = request.args.get("q", "").strip()[:50]
    posts, issues = [], []
    if len(q) >= 2:
        like = f"%{q}%"  # ponytail: LIKE 검색. 글이 수만 건 넘으면 FULLTEXT 인덱스로
        posts = m.rows(f"{POST_SQL} WHERE p.status=1 AND (p.title LIKE %s OR p.content LIKE %s) ORDER BY p.id DESC LIMIT 50", (like, like))
        issues = m.rows("SELECT * FROM issues WHERE approved=1 AND title LIKE %s ORDER BY id DESC LIMIT 20", (like,))
        for i in issues:
            if i["closed"]:
                with_yp([i])
            else:
                i["part"] = i["yes_cnt"] + i["no_cnt"]
                i["yes_cnt"] = i["no_cnt"] = None
    return render_template("search.html", q=q, posts=posts, issues=issues)


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
    cs = m.rows("SELECT c.*, COALESCE(u.nickname, c.anon_nick) nick, u.points upts FROM comments c LEFT JOIN users u ON u.id=c.user_id "
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
    elif target == "meetup":
        if not m.one("SELECT 1 FROM meetups WHERE id=%s AND status=1", (tid,)):
            abort(404)
        back = url_for("meetup", mid=tid)
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
    if target not in ("post", "comment", "meetup") or not tid:
        abort(400)
    uid, ah = ident()
    rh = f"u{uid}" if uid else ah
    try:
        m.run("INSERT INTO reports(target,target_id,reason,reporter_hash) VALUES(%s,%s,%s,%s)",
              (target, tid, request.form.get("reason", "")[:50], rh))
    except pymysql.err.IntegrityError:
        return jsonify(ok=False, msg="이미 신고함"), 409
    tbl = {"post": "posts", "comment": "comments", "meetup": "meetups"}[target]
    m.run(f"UPDATE {tbl} SET status=0 WHERE id=%s AND (SELECT COUNT(*) FROM reports WHERE target=%s AND target_id=%s)>=%s",
          (tid, target, tid, app.config["REPORT_BLIND"]))
    return jsonify(ok=True)


# ─── 모임 ───────────────────────────────────────────────

MEETUP_SQL = ("SELECT mt.*, COALESCE(u.nickname, mt.anon_nick) nick, u.points upts, "
              "(SELECT COUNT(*) FROM meetup_members mm WHERE mm.meetup_id=mt.id) members "
              "FROM meetups mt LEFT JOIN users u ON u.id=mt.user_id")


def meetup_fields(form):
    """개설/수정 공통 입력 검증 → dict 또는 ValueError."""
    title, desc = form.get("title", "").strip()[:120], form.get("description", "").strip()
    place = form.get("place", "").strip()[:100] or None
    cap = form.get("capacity", type=int)
    try:
        meet_at = datetime.strptime(form.get("meet_at", ""), "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("모임 일시를 정하셈")
    if not title or not desc:
        raise ValueError("제목과 설명은 필수")
    if meet_at < now_kst():
        raise ValueError("이미 지난 시각임")
    if cap is not None and not (2 <= cap <= 500):
        raise ValueError("정원은 2~500명")
    return {"title": title, "description": desc, "place": place, "meet_at": meet_at, "capacity": cap}


@app.get("/meetup")
def meetup_list():
    upcoming = m.rows(f"{MEETUP_SQL} WHERE mt.status=1 AND mt.meet_at>=NOW() ORDER BY mt.meet_at LIMIT 60")
    past = m.rows(f"{MEETUP_SQL} WHERE mt.status=1 AND mt.meet_at<NOW() ORDER BY mt.meet_at DESC LIMIT 12")
    return render_template("meetups.html", upcoming=upcoming, past=past)


@app.route("/meetup/new", methods=["GET", "POST"])
def meetup_new():
    if request.method == "POST":
        try:
            f = meetup_fields(request.form)
            a = author_fields(request.form)
        except ValueError as e:
            flash(str(e))
            return render_template("meetup_form.html", mt=request.form, edit=False)
        mid = m.run("INSERT INTO meetups(user_id, anon_nick, anon_pw_hash, title, description, place, meet_at, capacity) VALUES(%s,%s,%s,%s,%s,%s,%s,%s)",
                    (a["user_id"], a["anon_nick"], a["anon_pw_hash"], f["title"], f["description"], f["place"], f["meet_at"], f["capacity"]))
        if save_image(request.files.get("image"), f"m{mid}"):
            m.run("UPDATE meetups SET has_image=1 WHERE id=%s", (mid,))
        uid, ah = ident()  # 개설자는 자동 참여
        m.run("INSERT IGNORE INTO meetup_members(meetup_id, user_id, anon_hash, nick) VALUES(%s,%s,%s,%s)", (mid, uid, ah, a["anon_nick"] or session["nick"]))
        return redirect(url_for("meetup", mid=mid))
    return render_template("meetup_form.html", mt=None, edit=False)


@app.get("/meetup/<int:mid>")
def meetup(mid):
    mt = m.one(f"{MEETUP_SQL} WHERE mt.id=%s", (mid,))
    if not mt:
        abort(404)
    if mt["status"] == 0:
        return render_template("meetup.html", mt=mt, blind=True, members=[], joined=False, comments=[], can_edit=False)
    uid, ah = ident()
    members = m.rows("SELECT mm.*, u.points upts FROM meetup_members mm LEFT JOIN users u ON u.id=mm.user_id WHERE mm.meetup_id=%s ORDER BY mm.id", (mid,))
    joined = any((uid and x["user_id"] == uid) or (ah and x["anon_hash"] == ah) for x in members)
    return render_template("meetup.html", mt=mt, blind=False, members=members, joined=joined,
                           comments=load_comments("meetup", mid), can_edit=mt["user_id"] is None or mt["user_id"] == uid,
                           cmt_n=m.one("SELECT COUNT(*) n FROM comments WHERE target='meetup' AND target_id=%s", (mid,))["n"],
                           full=mt["capacity"] is not None and mt["members"] >= mt["capacity"], past=mt["meet_at"] < now_kst())


@app.post("/meetup/<int:mid>/join")
def meetup_join(mid):
    mt = m.one(f"{MEETUP_SQL} WHERE mt.id=%s AND mt.status=1", (mid,))
    if not mt:
        abort(404)
    uid, ah = ident()
    back = url_for("meetup", mid=mid)
    if m.affect("DELETE FROM meetup_members WHERE meetup_id=%s AND (user_id=%s OR anon_hash=%s)", (mid, uid, ah)):
        flash("참여 취소함")
        return redirect(back)
    if mt["meet_at"] < now_kst():
        flash("이미 끝난 모임임")
    elif mt["capacity"] is not None and mt["members"] >= mt["capacity"]:
        flash("정원이 다 찼음")
    else:
        nick = session.get("nick") or request.form.get("nick", "").strip()
        if not NICK_RE.match(nick or ""):
            flash("참여할 닉네임을 2~12자로 적으셈")
        else:
            m.run("INSERT IGNORE INTO meetup_members(meetup_id, user_id, anon_hash, nick) VALUES(%s,%s,%s,%s)", (mid, uid, ah, nick))
            flash("참여 완료")
    return redirect(back)


@app.route("/meetup/<int:mid>/edit", methods=["GET", "POST"])
def meetup_edit(mid):
    mt = m.one("SELECT * FROM meetups WHERE id=%s AND status=1", (mid,))
    if not mt:
        abort(404)
    if mt["user_id"] and mt["user_id"] != session.get("uid"):
        abort(403)
    if request.method == "POST":
        try:
            if not is_owner(mt, request.form.get("pw", "")):
                raise ValueError("비밀번호가 틀림")
            f = meetup_fields(request.form)
        except ValueError as e:
            flash(str(e))
        else:
            m.run("UPDATE meetups SET title=%s, description=%s, place=%s, meet_at=%s, capacity=%s WHERE id=%s",
                  (f["title"], f["description"], f["place"], f["meet_at"], f["capacity"], mid))
            if save_image(request.files.get("image"), f"m{mid}"):
                m.run("UPDATE meetups SET has_image=1 WHERE id=%s", (mid,))
            return redirect(url_for("meetup", mid=mid))
    return render_template("meetup_form.html", mt=mt, edit=True)


@app.post("/meetup/<int:mid>/delete")
def meetup_delete(mid):
    mt = m.one("SELECT * FROM meetups WHERE id=%s", (mid,))
    if not mt:
        abort(404)
    if not is_owner(mt, request.form.get("pw", "")):
        return jsonify(ok=False, msg="비밀번호가 틀림"), 403
    m.run("DELETE FROM comments WHERE target='meetup' AND target_id=%s", (mid,))
    m.run("DELETE FROM meetups WHERE id=%s", (mid,))
    return jsonify(ok=True, to=url_for("meetup_list"))


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
    if not edit and request.args.get("title"):  # 뉴스 → 이슈 등록 프리필
        edit = {"id": None, "title": request.args.get("title", "")[:200], "summary": "", "category": None,
                "is_main": 0, "opt_yes": "", "opt_no": "", "open_date": now_kst().date(), "approved": 1}
    issues = m.rows("SELECT *, yes_cnt+no_cnt part FROM issues ORDER BY open_date DESC, is_main DESC, id DESC LIMIT 60")
    return render_template("admin/issues.html", issues=issues, edit=edit, today=now_kst().date())


@app.route("/admin/news", methods=["GET", "POST"])
@admin_required
def admin_news():
    if request.method == "POST":
        title, url_ = request.form.get("title", "").strip()[:200], request.form.get("url", "").strip()[:500]
        if title and url_.startswith("http"):
            image = request.form.get("image", "").strip()[:500] or None
            if not image:
                import issue_gen
                image = issue_gen.og_image(url_)
            m.run("INSERT IGNORE INTO news(title, url, source, image, pinned) VALUES(%s,%s,%s,%s,%s)",
                  (title, url_, request.form.get("source", "").strip()[:50] or None, image, 1 if request.form.get("pinned") else 0))
        else:
            flash("제목과 http로 시작하는 링크가 필요함")
        return redirect(url_for("admin_news"))
    items = m.rows("SELECT * FROM news ORDER BY pinned DESC, id DESC LIMIT 100")
    return render_template("admin/news.html", items=items)


@app.post("/admin/news/fetch")
@admin_required
def admin_news_fetch():
    try:
        flash(fetch_news_now())
    except Exception as e:
        flash(f"뉴스 수집 실패: {e}")
    return redirect(url_for("admin_news"))


@app.post("/admin/news/<int:nid>/<action>")
@admin_required
def admin_news_action(nid, action):
    if action == "delete":
        m.run("DELETE FROM news WHERE id=%s", (nid,))
    elif action == "pin":
        m.run("UPDATE news SET pinned=1-pinned WHERE id=%s", (nid,))
    return redirect(url_for("admin_news"))


@app.post("/admin/issues/generate")
@admin_required
def admin_issues_generate():
    try:
        flash(gen_issues_today())
    except Exception as e:
        flash(f"이슈 생성 실패: {e}")
    return redirect(url_for("admin_issues"))


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
        if target in ("post", "comment", "meetup") and status in (0, 1):
            tbl = {"post": "posts", "comment": "comments", "meetup": "meetups"}[target]
            m.run(f"UPDATE {tbl} SET status=%s WHERE id=%s", (status, tid))
        return redirect(url_for("admin_blind"))
    items = m.rows(
        "SELECT r.target, r.target_id, COUNT(*) n, GROUP_CONCAT(r.reason SEPARATOR ' / ') reasons, "
        "COALESCE(p.title, c.content, mt.title) txt, COALESCE(p.status, c.status, mt.status) status "
        "FROM reports r LEFT JOIN posts p ON r.target='post' AND p.id=r.target_id "
        "LEFT JOIN comments c ON r.target='comment' AND c.id=r.target_id "
        "LEFT JOIN meetups mt ON r.target='meetup' AND mt.id=r.target_id "
        "GROUP BY r.target, r.target_id, p.title, c.content, mt.title, p.status, c.status, mt.status ORDER BY status ASC, n DESC LIMIT 200")
    return render_template("admin/blind.html", items=items)


if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5050)))
