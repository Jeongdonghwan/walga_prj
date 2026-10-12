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
