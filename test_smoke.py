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
db("INSERT INTO issues(title, open_date) VALUES('이슈', CURDATE())")
assert anon.post("/comment", data={"target": "issue", "target_id": 1, "content": "x", "nick": "a", "pw": "1234"}).status_code == 403

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
