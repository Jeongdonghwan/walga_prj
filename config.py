# -*- coding: utf-8 -*-
"""환경변수 우선 설정. .env 파일이 있으면 로드한다 (.env.example 참고)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY") or os.urandom(24).hex()

    DB_HOST = os.environ.get("DB_HOST", "127.0.0.1")
    DB_PORT = int(os.environ.get("DB_PORT", "3306"))
    DB_NAME = os.environ.get("DB_NAME", "walga")
    DB_USER = os.environ.get("DB_USER", "root")
    DB_PASSWORD = os.environ.get("DB_PASSWORD", "")

    ADMIN_ID = os.environ.get("ADMIN_ID")
    ADMIN_PW = os.environ.get("ADMIN_PW")
    SITE_URL = os.environ.get("SITE_URL")

    AD_ENABLED = os.environ.get("AD_ENABLED", "0") == "1"

    # 게시판 (키=URL, 값=표시명). 추가·분리는 여기 한 줄. 정치 게시판은 두지 않음 (이슈 댓글로)
    BOARDS = {"free": "자유", "humor": "유머", "work": "직장", "money": "돈·재테크",
              "love": "연애·결혼", "sports": "스포츠", "it": "디지털·IT", "life": "일상·생활"}

    BEST_THRESHOLD = 20      # 추천 N 도달 시 왈베 승격
    POINTS_WIN = 10          # 마감 시 다수파 적중 포인트
    AUTO_PUBLISH = os.environ.get("AUTO_PUBLISH", "0") == "1"   # 자동 생성 이슈를 승인 없이 바로 게시
    ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
    REPORT_BLIND = 5         # 신고 N건 누적 시 자동 블라인드
    PER_PAGE = 20
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024   # 업로드 5MB
    IMG_MAX_SIDE = 1200
    UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "uploads")
