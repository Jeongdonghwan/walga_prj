# -*- coding: utf-8 -*-
"""Phase 1 스모크 테스트. 실행: python test_smoke.py  (로컬 MariaDB에 walga_test DB를 새로 만들고 지움)"""
import os
import sys

os.environ["DB_NAME"] = "walga_test"
import pymysql  # noqa: E402

from config import Config  # noqa: E402

root = pymysql.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER,
                       password=Config.DB_PASSWORD, charset="utf8mb4", autocommit=True)
cur = root.cursor()
cur.execute("DROP DATABASE IF EXISTS walga_test")
for stmt in open("schema.sql", encoding="utf-8").read().replace("walga", "walga_test").split(";"):
    if stmt.strip():
        cur.execute(stmt)

import app as A  # noqa: E402

A.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, BEST_THRESHOLD=5, REPORT_BLIND=3)


def client(uid):
    c = A.app.test_client()
    c.set_cookie("wuid", uid)
    return c


def db(sql, args=()):
    cur.execute("USE walga_test")
    cur.execute(sql, args)
    return cur.fetchone()


anon = client("anon1")

# 1. 유동닉 글쓰기 (검증 실패 → 성공)
r = anon.post("/write", data={"board": "free", "title": "t", "content": "c", "nick": "x", "pw": "1234"})
assert r.status_code == 200 and "닉네임은" in r.get_data(as_text=True), "닉네임 검증"
r = anon.post("/write", data={"board": "free", "title": "테스트글", "content": "본문\n둘째줄", "nick": "유동닉", "pw": "1234"})
assert r.status_code == 302 and r.headers["Location"].endswith("/post/1"), r.status_code
html = anon.get("/post/1").get_data(as_text=True)
assert "테스트글" in html and "본문<br>둘째줄" in html and "유동닉" in html

# 2. 회원가입 → 로그인 세션으로 글쓰기
user = A.app.test_client()
assert user.post("/signup", data={"nickname": "tester", "password": "pass1234"}).status_code == 302
assert user.post("/write", data={"board": "humor", "title": "회원 글", "content": "ㅋㅋ"}).status_code == 302
assert "회원 글" in user.get("/board/humor").get_data(as_text=True)
# 회원 닉네임을 유동닉으로 사칭 불가
r = anon.post("/write", data={"board": "free", "title": "사칭", "content": "x", "nick": "tester", "pw": "1234"})
assert "회원 닉네임" in r.get_data(as_text=True)

# 3. 댓글 + 답글
anon2 = client("anon2")
assert anon2.post("/comment", data={"target": "post", "target_id": 1, "content": "댓글1", "nick": "댓글러", "pw": "0000"}).status_code == 302
assert user.post("/comment", data={"target": "post", "target_id": 1, "parent_id": 1, "content": "답글"}).status_code == 302
assert db("SELECT cmt_cnt FROM posts WHERE id=1")[0] == 2
html = anon.get("/post/1").get_data(as_text=True)
assert "댓글1" in html and "답글" in html and 'class="cmt reply"' in html

# 4. 추천: 중복 409, 임계치 도달 시 왈베 승격 (핵심 규칙 3)
assert user.post("/rec", data={"target": "post", "target_id": 1}).get_json()["ok"]
assert user.post("/rec", data={"target": "post", "target_id": 1}).status_code == 409
for i in range(4):
    assert client(f"rec{i}").post("/rec", data={"target": "post", "target_id": 1}).get_json()["rec_cnt"] == i + 2
row = db("SELECT rec_cnt, is_best, best_at FROM posts WHERE id=1")
assert row == (5, 1, row[2]) and row[2] is not None, row
assert "테스트글" in anon.get("/best").get_data(as_text=True)

# 5. 신고 누적 → 자동 블라인드, 중복 신고 409
for i in range(3):
    assert client(f"rep{i}").post("/report", data={"target": "post", "target_id": 1, "reason": "욕설"}).get_json()["ok"]
assert client("rep0").post("/report", data={"target": "post", "target_id": 1}).status_code == 409
assert db("SELECT status FROM posts WHERE id=1")[0] == 0
assert "블라인드된 글" in anon.get("/post/1").get_data(as_text=True)
assert "테스트글" not in anon.get("/board/free").get_data(as_text=True)

# 6. 어드민: 인증 없으면 401, 블라인드 해제
assert anon.get("/admin/blind").status_code == 401
auth = {"Authorization": "Basic " + __import__("base64").b64encode(b"admin:1234").decode()}
assert "욕설" in anon.get("/admin/blind", headers=auth).get_data(as_text=True)
anon.post("/admin/blind", headers=auth, data={"target": "post", "target_id": 1, "status": 1})
assert db("SELECT status FROM posts WHERE id=1")[0] == 1

# 7. 이슈 댓글은 투표자만 (핵심 규칙 2)
db("INSERT INTO issues(title, open_date, is_main, slug) VALUES('이슈', CURDATE(), 1, 'issue-1')")
assert anon.post("/comment", data={"target": "issue", "target_id": 1, "content": "x", "nick": "a", "pw": "1234"}).status_code == 403
assert anon.get("/issue/1").status_code == 301  # slug 캐노니컬
html = anon.get("/issue/1/issue-1").get_data(as_text=True)
assert "토론에 참여할 수 있음" in html and "%" not in html.split('<main')[1].split('토론')[0], "진행 중엔 결과 숨김"

# 8. 투표: 중복 차단, 투표 후 댓글 허용, 홈 '투표 완료' 상태, 집계 비공개 유지 (핵심 규칙 1)
assert anon.post("/vote", data={"issue_id": 1, "choice": 1}).status_code == 302
assert "이미 투표함" in anon.post("/vote", data={"issue_id": 1, "choice": 0}, follow_redirects=True).get_data(as_text=True)
assert anon.post("/comment", data={"target": "issue", "target_id": 1, "content": "토론", "nick": "a", "pw": "1234"}).status_code == 302
html = anon.get("/issue/1/issue-1").get_data(as_text=True)
assert "투표 완료" in html and "토론" in html and "%" not in html.split('class="vbox"')[1].split('</div>')[0]
assert "투표 완료" in anon.get("/").get_data(as_text=True)
assert db("SELECT yes_cnt, no_cnt FROM issues WHERE id=1") == (1, 0)
assert user.post("/vote", data={"issue_id": 1, "choice": 1}).status_code == 302
assert db("SELECT streak, last_vote_date=CURDATE() FROM users WHERE id=1") == (1, 1)
assert db("SELECT COUNT(*) FROM attendance WHERE user_id=1")[0] == 1

# 9. 지연 마감: 날짜가 지나면 다음 요청에서 닫히고 다수파(+10P) 지급, 결과 공개
db("UPDATE issues SET open_date=CURDATE()-INTERVAL 1 DAY WHERE id=1")
html = anon.get("/issue").get_data(as_text=True)
assert db("SELECT closed FROM issues WHERE id=1")[0] == 1
assert db("SELECT points FROM users WHERE id=1")[0] == 10
assert "100%" in html and "다수파" in html
html = anon.get("/issue/1/issue-1").get_data(as_text=True)
assert "찬성 100%" in html and "적중" in html
assert "마감된 이슈임" in client("late").post("/vote", data={"issue_id": 1, "choice": 0}, follow_redirects=True).get_data(as_text=True)
assert db("SELECT points FROM users WHERE id=1")[0] == 10, "마감 재실행 시 중복 지급 없음"

# 10. 어드민 이슈 등록·메인 지정·승인 대기 비노출
assert anon.get("/admin/issues", headers=auth).status_code == 200
anon.post("/admin/issues", headers=auth, data={"title": "새 이슈", "summary": "요약", "opt_yes": "찬성", "opt_no": "반대", "is_main": "on", "approved": "on"})
anon.post("/admin/issues", headers=auth, data={"title": "대기 이슈"})  # approved 미체크 → 0
assert db("SELECT COUNT(*) FROM issues WHERE is_main=1 AND open_date=CURDATE()")[0] == 1
html = anon.get("/").get_data(as_text=True)
assert "새 이슈" in html and "대기 이슈" not in html
assert anon.get("/issue/3").status_code == 404

# 11. 마이: 비로그인 리다이렉트, 스탯·히스토리, 비밀번호 변경
assert anon.get("/my").status_code == 302
html = user.get("/my").get_data(as_text=True)
assert "tester" in html and "1회" in html and "100%" in html and "적중" in html and "회원 글" in html
assert "틀림" in user.post("/my/password", data={"cur": "nope", "new": "abcd1234"}, follow_redirects=True).get_data(as_text=True)
assert "변경됨" in user.post("/my/password", data={"cur": "pass1234", "new": "abcd1234"}, follow_redirects=True).get_data(as_text=True)
assert A.app.test_client().post("/login", data={"nickname": "tester", "password": "abcd1234"}).status_code == 302

# 12. 검색 · sitemap · robots · 결과 카드
html = anon.get("/search?q=테스트").get_data(as_text=True)
assert "테스트글" in html and "검색 결과" in html
assert "두 글자" in anon.get("/search?q=a").get_data(as_text=True)
assert "/issue/1/issue-1" in anon.get("/sitemap.xml").get_data(as_text=True)
assert "Sitemap:" in anon.get("/robots.txt").get_data(as_text=True)
assert os.path.exists(os.path.join(A.CARD_DIR, "1.png")), "마감 시 결과 카드 생성"
assert 'og:image' in anon.get("/issue/1/issue-1").get_data(as_text=True)
assert client("crawler").get("/issue/1/issue-1").headers.get("Cache-Control", "").startswith("public")
assert not anon.get("/issue/1/issue-1").headers.get("Cache-Control", "").startswith("public"), "투표자 페이지는 개인화라 캐시 금지"
assert "Lv." in user.get("/my").get_data(as_text=True)
os.remove(os.path.join(A.CARD_DIR, "1.png"))

# 13. 이슈 생성 파서 (API 호출 없이)
import issue_gen
mn, iss = issue_gen.parse_issues('```json\n{"main": {"title": "축의금 5만원이면 충분?", "summary": "s", "category": "일상", "opt_yes": "충분", "opt_no": "부족"}, '
                                 '"issues": [{"title": "t1"}, {"title": "t2", "opt_yes": ""}]}\n```')
assert mn["opt_yes"] == "충분" and iss[1]["opt_yes"] == "찬성" and len(iss) == 2

# 14. 뉴스: RSS 파서, 어드민 등록·픽·삭제, 홈·목록 노출, 이슈 프리필
rss = '<rss xmlns:media="http://search.yahoo.com/mrss/"><channel><item><title> 가나다라마바사아 A </title><link>https://x.test/a</link><media:content url="https://x.test/a.jpg" type="image/jpeg"/></item><item><title>가나다라마바사 B</title><link>https://x.test/b</link><description><![CDATA[<p><img src="https://x.test/b.png"></p>]]></description></item><item><title>No link</title></item><item><title>클로징</title><link>https://x.test/c</link></item></channel></rss>'.encode()
parsed = issue_gen.parse_rss(rss, "매체")
assert len(parsed) == 2 and parsed[0] == {"title": "가나다라마바사아 A", "url": "https://x.test/a", "source": "매체", "image": "https://x.test/a.jpg"} and parsed[1]["image"] == "https://x.test/b.png"
assert issue_gen.og_image("http://127.0.0.1:9/none") is None
anon.post("/admin/news", headers=auth, data={"title": "뉴스 하나", "url": "http://127.0.0.1:9/1", "source": "매체", "image": "https://img.test/1.jpg", "pinned": "on"})
anon.post("/admin/news", headers=auth, data={"title": "주소 없음", "url": "ftp://bad"})
assert db("SELECT COUNT(*), SUM(pinned) FROM news") == (1, 1)
html = anon.get("/").get_data(as_text=True)
assert "뉴스 하나" in html and "운영진 픽" in html and 'target="_blank"' in html and "https://img.test/1.jpg" in html
assert "뉴스 하나" in anon.get("/news").get_data(as_text=True)
assert 'value="뉴스 하나"' in anon.get("/admin/issues?title=뉴스 하나", headers=auth).get_data(as_text=True)
anon.post("/admin/news/1/pin", headers=auth)
assert db("SELECT pinned FROM news WHERE id=1")[0] == 0
anon.post("/admin/news/1/delete", headers=auth)
assert db("SELECT COUNT(*) FROM news")[0] == 0

# 15. 사이드바 위젯: 2단 페이지엔 오늘의 투표·실시간 왈베·뉴스 위젯 + 로그인 카드, 1단 페이지엔 없음
html = anon.get("/board/free").get_data(as_text=True)
assert "오늘의 투표" in html and "실시간 왈베" in html and 'class="card login"' in html and "wlist" in html
assert anon.post("/vote", data={"issue_id": 2, "choice": 1}).status_code == 302
anon.get("/board/humor")  # flash 소비
assert 'class="wdone"' in anon.get("/board/humor").get_data(as_text=True), "투표한 방문자는 위젯에도 완료 표시"
assert "오늘의 투표" in anon.get("/post/1").get_data(as_text=True)
assert "오늘의 투표" not in anon.get("/write?board=free").get_data(as_text=True)
assert 'class="card profile"' in user.get("/board/free").get_data(as_text=True)

# 16. 모임: 개설(검증), 목록, 참여/취소, 정원, 댓글, 신고 블라인드, 삭제
from datetime import datetime, timedelta
soon = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
r = anon.post("/meetup/new", data={"title": "러닝", "description": "d", "nick": "모임장", "pw": "1234"})
assert r.status_code == 200 and "일시" in r.get_data(as_text=True)
r = anon.post("/meetup/new", data={"title": "한강 러닝", "description": "같이 뜀", "place": "뚝섬", "meet_at": soon, "capacity": 2, "nick": "모임장", "pw": "1234"})
assert r.status_code == 302 and r.headers["Location"].endswith("/meetup/1")
html = anon.get("/meetup").get_data(as_text=True)
assert "한강 러닝" in html and "1/2" in html and "D-3" in html
html = anon.get("/meetup/1").get_data(as_text=True)
assert "참여 중" in html and "모임장" in html, "개설자는 자동 참여"
assert "닉네임을" in anon2.post("/meetup/1/join", follow_redirects=True).get_data(as_text=True)
assert "회원 닉네임" in anon2.post("/meetup/1/join", data={"nick": "tester"}, follow_redirects=True).get_data(as_text=True)
assert "이미 있음" in anon2.post("/meetup/1/join", data={"nick": "모임장"}, follow_redirects=True).get_data(as_text=True)
assert "참여 완료" in anon2.post("/meetup/1/join", data={"nick": "러너"}, follow_redirects=True).get_data(as_text=True)
assert "정원이 다 찼음" in user.post("/meetup/1/join", follow_redirects=True).get_data(as_text=True)
assert "참여 취소함" in anon2.post("/meetup/1/join", follow_redirects=True).get_data(as_text=True)
assert "참여 완료" in user.post("/meetup/1/join", follow_redirects=True).get_data(as_text=True)
assert db("SELECT COUNT(*) FROM meetup_members WHERE meetup_id=1")[0] == 2
assert anon2.post("/comment", data={"target": "meetup", "target_id": 1, "content": "몇 시에 모임?", "nick": "러너", "pw": "0000"}).status_code == 302
assert "몇 시에 모임?" in anon.get("/meetup/1").get_data(as_text=True)
for i in range(3):
    assert client(f"mrep{i}").post("/report", data={"target": "meetup", "target_id": 1}).get_json()["ok"]
html = anon.get("/meetup/1").get_data(as_text=True)
assert "블라인드된 모임" in html and "같이 뜀" not in html and "og:title" not in html, "블라인드 시 head 메타도 숨김"
assert "모임 #1" in anon.get("/admin/blind", headers=auth).get_data(as_text=True)
anon.post("/admin/blind", headers=auth, data={"target": "meetup", "target_id": 1, "status": 1})
past = (datetime.now() - timedelta(days=1)).replace(second=0, microsecond=0)
db("UPDATE meetups SET meet_at=%s WHERE id=1", (past,))
r = anon.post("/meetup/1/edit", data={"pw": "1234", "title": "한강 러닝(장소 변경)", "description": "같이 뜀", "meet_at": past.strftime("%Y-%m-%dT%H:%M"), "capacity": 2})
assert r.status_code == 302 and db("SELECT title FROM meetups WHERE id=1")[0] == "한강 러닝(장소 변경)", "지난 모임도 일시 그대로면 수정 가능"
r = anon.post("/meetup/1/edit", data={"pw": "1234", "title": "x", "description": "d", "meet_at": (past - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"), "capacity": 2})
assert r.status_code == 200 and "지난 시각" in r.get_data(as_text=True), "일시를 과거로 바꾸는 건 거부"
assert user.post("/meetup/1/delete").status_code == 403, "회원이 유동닉 모임 삭제 불가(비번 없음)"
assert anon.post("/meetup/1/delete", data={"pw": "1234"}).get_json()["ok"]
assert anon.get("/meetup/1").status_code == 404 and db("SELECT COUNT(*) FROM meetup_members")[0] == 0

# 8. 삭제: 비밀번호 틀림 403, 맞으면 삭제 / 회원 글은 본인만
assert anon.post("/comment/1/delete", data={"pw": "9999"}).status_code == 403
assert anon.post("/comment/1/delete", data={"pw": "0000"}).get_json()["ok"]
assert db("SELECT cmt_cnt FROM posts WHERE id=1")[0] == 1
assert anon.post("/post/2/delete", data={"pw": "1234"}).status_code == 403
assert user.post("/post/2/delete").get_json()["ok"]
assert anon.post("/post/1/delete", data={"pw": "1234"}).get_json()["to"] == "/board/free"
assert anon.get("/post/1").status_code == 404 and db("SELECT COUNT(*) FROM comments")[0] == 0

cur.execute("DROP DATABASE walga_test")
print("smoke ok")
sys.exit(0)
