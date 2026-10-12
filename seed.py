# -*- coding: utf-8 -*-
"""목업 데이터 주입. 실행: python seed.py  (.env의 DB 설정 사용)
회원 6명(레벨 분포) · 글 12개 · 댓글 6개 · 오늘 이슈 6개 · 어제 결과 3개 · 아카이브 8개 · 투표 기록.
이미 글이 있으면 건너뜀 (여러 번 실행해도 안전). 실서버 초기 화면 채우기용 — 운영 데이터가 쌓이면 지워도 됨."""
import random
import re

import pymysql
from werkzeug.security import generate_password_hash

from config import Config

PW = "pass1234"
MEMBERS = {"짤줍는사람": 1250, "재테크초보": 90, "자취만렙": 430, "축의금고민": 20, "점심난민": 0, "도보10분": 160, "퇴근요정": 180}
POSTS = [  # (board, nick, title, rec, cmt, is_best)
    ("humor", "짤줍는사람", "회사 단톡방에 사표 잘못 보낸 사람 업데이트함", 214, 6, 1),
    ("life", "점심난민", "다들 점심값 얼마까지 씀? 만원 넘으면 좀 그런가", 67, 0, 0),
    ("humor", "도보10분", "배달비 아까워서 픽업 갔다가 생긴 일", 142, 0, 1),
    ("work", "퇴근요정", "퇴근하고 아무것도 하기 싫은 거 정상임?", 38, 0, 0),
    ("money", "재테크초보", "월급 280 저축 포트폴리오 피드백 받고 수정했다", 187, 0, 1),
    ("love", "솔로3년차", "소개팅에서 더치페이 얘기 먼저 꺼내는 거 어떰", 92, 0, 0),
    ("humor", "익명767", "ㅋㅋ 우리 팀장 재택인데 상태메시지 실화냐", 29, 0, 0),
    ("love", "축의금고민", "축의금 5만원 냈다가 한소리 들은 썰 푼다", 120, 0, 1),
    ("money", "이사준비중", "전세 알아보는데 집주인이 이런 말 하는 거 정상?", 31, 0, 0),
    ("humor", "짤줍는사람", "국회 회의록에서 발견한 역대급 티키타카", 128, 0, 1),
    ("life", "자취만렙", "자취 8년차가 말아주는 만원의 행복 장보기", 119, 0, 1),
    ("life", "3대백오십", "헬스장 삼대운동 하는 날 vs 하기 싫은 날", 9, 0, 0),
]
BODY = "본문임.\n\n다들 어떻게 생각함? 나는 이게 좀 아닌 것 같은데\n댓글로 의견 좀."
TODAY = [  # (title, summary, is_main, part)
    ("주 4일제 도입, 월급 10% 깎여도 찬성?", "노동계가 주 4일제 시범사업 확대를 요구하고 나섰음. 경영계는 임금 조정 없이는 불가 입장. 임금 10% 조정을 전제로 한다면?", 1, 5318),
    ("여야 추경 협상 이틀째 결렬, 쟁점 정리해봄", "", 0, 2847),
    ("한은 또 금리 동결. 대출 있는 사람들 어떻게 봄?", "", 0, 1932),
    ("청년 주거지원안 다음주 발표 예정, 기대 vs 글쎄", "", 0, 1204),
    ("국회의원 정수 축소론 재점화. 이번엔 진짜 될까", "", 0, 4182),
    ("플랫폼 규제법 공청회 요약. 찬반 논리 둘 다 정리함", "", 0, 886),
]
YDAY = [("결혼식 축의금, 5만원이면 충분하다?", 2889, 4158, "충분", "부족"),
        ("국민연금 보험료율 인상, 받아들일 수 있다?", 1621, 3291, "수용", "반대"),
        ("소개팅 첫 만남 더치페이가 맞다?", 1613, 1375, "맞다", "아니다")]
ARCHIVE = [("지하철 노약자석, 임산부가 우선?", 66, "임산부", "노약자"), ("최저임금 업종별 차등 적용?", 48, "찬성", "반대"),
           ("반려견 카페 출입 제한 확대?", 57, "찬성", "반대"), ("회식 불참, 눈치 안 봐도 된다?", 71, "된다", "안 된다"),
           ("점심값 만원 넘으면 사치다?", 39, "사치", "정상"), ("카톡 답장 3시간 넘으면 무례?", 52, "무례", "괜찮다"),
           ("배달비 4천원, 그냥 픽업이 답?", 63, "픽업", "배달"), ("신입 연봉 공개 문화, 찬성?", 58, "찬성", "반대")]


def slugify(t):
    return re.sub(r"[^\w가-힣]+", "-", t).strip("-")[:80] or "issue"


def main():
    c = pymysql.connect(host=Config.DB_HOST, port=Config.DB_PORT, user=Config.DB_USER, password=Config.DB_PASSWORD,
                        database=Config.DB_NAME, charset="utf8mb4", autocommit=True, init_command="SET time_zone='+09:00'")
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM posts")
    if cur.fetchone()[0]:
        print("이미 글이 있어서 건너뜀")
        return
    pw = generate_password_hash(PW)
    for nick, pts in MEMBERS.items():
        cur.execute("INSERT IGNORE INTO users(nickname, pw_hash, points, streak, created_at) VALUES(%s,%s,%s,%s,NOW()-INTERVAL %s DAY)",
                    (nick, pw, pts, random.randint(0, 20), random.randint(5, 120)))
    cur.execute("SELECT id, nickname FROM users")
    uid = {n: i for i, n in cur.fetchall()}

    for board, nick, title, rec, cmt, best in POSTS:
        cur.execute("INSERT INTO posts(board, user_id, anon_nick, anon_pw_hash, title, content, rec_cnt, cmt_cnt, is_best, best_at, view_cnt, created_at) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,IF(%s,NOW(),NULL),%s,NOW()-INTERVAL %s MINUTE)",
                    (board, uid.get(nick), None if nick in uid else nick, None if nick in uid else pw, title, BODY, rec, cmt, best, best, rec * 7, random.randint(5, 600)))
    cur.execute("SELECT id FROM posts ORDER BY id LIMIT 1")
    pid = cur.fetchone()[0]
    for k, nick in enumerate(["짤줍는사람", "재테크초보", "자취만렙", "축의금고민", "도보10분"]):
        cur.execute("INSERT INTO comments(target, target_id, user_id, content, rec_cnt) VALUES('post',%s,%s,%s,%s)",
                    (pid, uid[nick], "ㅋㅋㅋ 이거 진짜임? 나도 비슷한 경험 있음", (k + 1) * 3))
    cur.execute("SELECT id FROM comments WHERE target_id=%s ORDER BY id LIMIT 1", (pid,))
    cur.execute("INSERT INTO comments(target, target_id, parent_id, user_id, content) VALUES('post',%s,%s,%s,'ㄹㅇ 공감')", (pid, cur.fetchone()[0], uid["퇴근요정"]))

    cur.execute("SELECT COUNT(*) FROM issues WHERE open_date=CURDATE()")
    if not cur.fetchone()[0]:
        for t, s, main_, part in TODAY:
            cur.execute("INSERT INTO issues(title, summary, category, is_main, open_date, yes_cnt, no_cnt, slug) VALUES(%s,%s,'사회',%s,CURDATE(),%s,%s,%s)",
                        (t, s, main_, part // 2, part - part // 2, slugify(t)))
    for t, y, n, oy, on in YDAY:
        cur.execute("INSERT INTO issues(title, summary, category, opt_yes, opt_no, open_date, closed, yes_cnt, no_cnt, view_cnt, slug) "
                    "VALUES(%s,'그때 그 논쟁. 결과는 이렇게 났음.','일상',%s,%s,CURDATE()-INTERVAL 1 DAY,1,%s,%s,%s,%s)", (t, oy, on, y, n, (y + n) * 3, slugify(t)))
    for k, (t, yp, oy, on) in enumerate(ARCHIVE):
        part = random.randint(1500, 6000)
        y = part * yp // 100
        cur.execute("INSERT INTO issues(title, summary, category, is_main, opt_yes, opt_no, open_date, closed, yes_cnt, no_cnt, view_cnt, slug) "
                    "VALUES(%s,'그때 그 논쟁. 결과는 이렇게 났음.','일상',1,%s,%s,CURDATE()-INTERVAL %s DAY,1,%s,%s,%s,%s)",
                    (t, oy, on, k + 2, y, part - y, part * 3, slugify(t)))
    cur.execute("SELECT id, yes_cnt, no_cnt FROM issues WHERE closed=1")
    for iid, y, n in cur.fetchall():
        for nick in MEMBERS:
            cur.execute("INSERT IGNORE INTO votes(issue_id, user_id, choice) VALUES(%s,%s,%s)",
                        (iid, uid[nick], 1 if (y > n) == (random.random() < 0.7) else 0))
    print(f"seeded: users {len(MEMBERS)}, posts {len(POSTS)}, issues {len(TODAY) + len(YDAY) + len(ARCHIVE)}. 회원 비밀번호 전부 {PW}")


if __name__ == "__main__":
    main()
