# 왈가 (walga) — CLAUDE.md

정치를 가장한 커뮤니티. 이슈+투표가 간판, 본체는 커뮤니티.
한 번에 한 Phase만 진행. 각 Phase 완료 후 보고하고 승인받고 다음으로.

## 스택
- Flask + Jinja2 SSR + MariaDB (Cafe24 가상서버), Gunicorn + Nginx
- JS 프레임워크 금지. 바닐라 JS 최소한만
- 디자인 원본: `walga_pc.html`, `walga_mobile.html` — **구조·정보 밀도·말투**의 기준. 스타일은 2026-10 결정으로 모던 톤(라운드 12px, 보더 최소, surface 배경, 아이콘 카운트)으로 교체함. 그라데이션/애니메이션 금지는 유지
- 브랜드: favicon.ico, walga_logo.svg, 컬러 --brand:#0e7c6f / --hot:#e5493a

## 핵심 규칙 (절대 변경 금지)
1. **투표 결과 비공개**: 진행 중 이슈는 참여자 수만 노출. 게이지·퍼센트는 마감(23:59 KST) 후에만 공개
2. **투표 후 토론**: 이슈 댓글 작성은 해당 이슈 투표자만 가능 (조회는 전체 공개)
3. **왈베 승격**: 추천 20 도달 시 is_best=1 자동 승격 (수정 가능한 상수로)
4. **유동닉**: 비로그인도 닉네임+비밀번호(4자리)로 글·댓글 작성 가능. 투표는 비로그인 시 쿠키+IP 해시로 중복 방지
5. **게시판은 config.BOARDS 8개**(자유·유머·직장·돈·재테크·연애·결혼·스포츠·디지털·IT·일상·생활). 정치 게시판 없음 — 정치 토론은 이슈 댓글로. 분리는 글이 쌓인 뒤
6. 이슈 페이지는 SEO 자산: 마감 후 `/issue/<id>/<slug>` 정적 캐시, meta/OG 자동 생성

## DB (MariaDB, utf8mb4)
```sql
users(id PK, nickname UNIQ, pw_hash, points INT DEF 0, streak INT DEF 0, last_vote_date DATE, created_at)
issues(id PK, title, summary TEXT, category VARCHAR(20), is_main TINYINT DEF 0,  -- 오늘의 왈가
  opt_yes VARCHAR(30) DEF '찬성', opt_no VARCHAR(30) DEF '반대',
  open_date DATE, closed TINYINT DEF 0, yes_cnt INT DEF 0, no_cnt INT DEF 0,
  view_cnt INT DEF 0, slug VARCHAR(200), created_at)
votes(id PK, issue_id FK, user_id NULL, anon_hash VARCHAR(64) NULL, choice TINYINT, created_at,
  UNIQ(issue_id,user_id), UNIQ(issue_id,anon_hash))
posts(id PK, board ENUM('free','humor'), user_id NULL, anon_nick, anon_pw_hash,
  title, content MEDIUMTEXT, has_image TINYINT DEF 0, rec_cnt INT DEF 0, cmt_cnt INT DEF 0,
  view_cnt INT DEF 0, is_best TINYINT DEF 0, best_at DATETIME NULL, status TINYINT DEF 1, created_at)
comments(id PK, target ENUM('post','issue'), target_id, parent_id NULL, user_id NULL,
  anon_nick, anon_pw_hash, content TEXT, rec_cnt INT DEF 0, status TINYINT DEF 1, created_at)
recommends(id PK, target ENUM('post','comment'), target_id, user_id NULL, anon_hash NULL, created_at,
  UNIQ(target,target_id,user_id), UNIQ(target,target_id,anon_hash))
reports(id PK, target ENUM('post','comment'), target_id, reason VARCHAR(50), reporter_hash, created_at)
attendance(id PK, user_id FK, date DATE, UNIQ(user_id,date))
```
- 신고 5건 누적 시 status=0 자동 블라인드 (명예훼손·선거법 리스크 대응, 1차 스펙 필수)

## 라우트
```
/                   홈 (오늘의 왈가 + 이슈5 + 왈베 + 자유 최신)
/issue              이슈 목록 (진행 중 / 어제의 결과 / 아카이브)
/issue/<id>/<slug>  이슈 상세 (요약+투표+댓글, 마감 시 결과 게이지)
/vote               POST 투표 (중복체크, 진행 중엔 결과 미반환)
/best               왈가베스트
/news               뉴스 (운영진 직접 등록 + Google News RSS 수집, 홈 중간 섹션 8개) — /admin/news, cron `0 */3 * * * flask --app app fetch-news`
/board/<free|humor> 게시판 목록 / /post/<id> 상세 / /write 작성
/rec, /comment, /report  POST
/my                 마이 (스트릭, 투표 히스토리, 적중률)
/admin              이슈 등록·수정, 블라인드 관리 (BasicAuth)
```

## 배치 (타이머 없음 — 2026-10 결정)
1. **마감**: APScheduler 대신 **지연 마감**. `close_due_issues()`가 홈/이슈/투표/마이 요청 시 `open_date < 오늘`인 이슈를 닫고(= 23:59 경과) 다수파 적중 +10P 지급 + 결과 카드 생성. 동시 요청은 rowcount로 1회만
2. **이슈 생성**: `issue_gen.py` — Google News RSS → Claude(claude-opus-5-5) → 메인 1 + 이슈 5 (커뮤 말투, 찬반 라벨). admin 「오늘 이슈 뽑기」 버튼 또는 cron `30 7 * * * cd /var/www/walga && ./venv/bin/flask --app app gen-issues`. 기본 승인 대기(`approved=0`), `AUTO_PUBLISH=1`이면 바로 게시
3. **결과 카드**: 마감 시 Pillow로 1200×630 생성(제목+게이지+"다음 판: 왈가에서") → /static/cards/<id>.png, 마감 이슈 og:image로 사용. 폰트 static/fonts/Pretendard-Bold.otf

## 광고
- 템플릿에 ad 슬롯 includes, `AD_ENABLED=False` 설정 하나로 전체 온오프
- 위치는 프로토타입의 점선 박스 그대로 (모바일 하단 앵커 금지)

## Phase
1. DB + 회원/유동닉 + 게시판 CRUD·추천·왈베 승격·신고 블라인드
2. 이슈·투표 (비공개 규칙, 투표 후 댓글 권한, 마감 배치)
3. 홈·마이·스트릭·포인트, PC/모바일 반응형 (프로토타입 픽셀 수준 재현)
4. 이슈 자동 생성 배치 + 결과 카드 + SEO (sitemap, OG, slug 캐시)
