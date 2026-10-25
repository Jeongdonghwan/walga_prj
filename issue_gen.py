# -*- coding: utf-8 -*-
"""뉴스 RSS → Claude(Opus 5.5)로 오늘의 이슈 6개(메인 1 + 일반 5) 생성.
호출: `flask --app app gen-issues` (cron) 또는 어드민 '오늘 이슈 뽑기' 버튼."""
import json
import re
import xml.etree.ElementTree as ET

import requests

FEEDS = {  # 언론사 공식 RSS (원문 링크·이미지 확보 가능). Google News는 리다이렉트 링크라 썸네일을 못 얻어 제외
    "한겨레": "https://www.hani.co.kr/rss/",
    "경향신문": "https://www.khan.co.kr/rss/rssdata/total_news.xml",
    "동아일보": "https://rss.donga.com/total.xml",
    "매일경제": "https://www.mk.co.kr/rss/30000001/",
    "JTBC": "https://fs.jtbc.co.kr/RSS/newsflash.xml",
    "조선일보": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml",
    "SBS": "https://news.sbs.co.kr/news/newsflashRssFeed.do?plink=RSSREADER",
    "오마이뉴스": "https://rss.ohmynews.com/rss/ohmynews.xml",
    "한국경제": "https://www.hankyung.com/feed/all-news",
}
HEADERS = {"User-Agent": "Mozilla/5.0 walga/1.0"}
MODEL = "claude-opus-5-5"

SYSTEM = """너는 한국 커뮤니티 '왈가'의 이슈 에디터다. 왈가는 매일 이슈 하나에 찬반 투표를 받고, 마감 후 결과를 공개하는 서비스다.
오늘 뉴스 헤드라인을 받아서 투표할 만한 이슈를 고르고 아래 JSON만 출력한다. 설명이나 코드펜스 없이 JSON 객체 하나만.

{"main": {"title": "...", "summary": "...", "category": "...", "opt_yes": "...", "opt_no": "..."},
 "issues": [{"title": "...", "summary": "...", "category": "...", "opt_yes": "...", "opt_no": "..."}, ... 5개]}

규칙
- title: 40자 이내. 커뮤니티 말투('~했음', '~함', '~임' 체)로 상황을 적고 질문으로 끝내거나 'A vs B' 형태. 예: "한은 또 금리 동결. 대출 있는 사람들 어떻게 봄?"
- summary: 2~3문장, 같은 말투. 배경 + 양쪽 입장을 공정하게. 특정 인물·단체 비방 금지.
- category: 정치/경제/사회/일상/문화/IT 중 하나
- opt_yes / opt_no: 2~6자 찬반 라벨. 질문에 맞게 ('찬성'/'반대', '된다'/'안 된다', '충분'/'부족' 등)
- main(오늘의 왈가)은 정치색이 약한 일상·생활 논쟁형을 우선 (돈, 직장, 연애·결혼, 예절, 소비). 헤드라인에 없으면 헤드라인에서 연상되는 생활 논쟁을 만들어도 됨.
- issues 5개는 헤드라인 기반. 정치 이슈는 정책 자체의 찬반으로 묻고, 특정 정당·후보 지지/반대를 유도하는 문장은 금지 (선거법). 명예훼손 소지가 있는 개인 사건은 제외.
- 6개 모두 서로 다른 주제."""

USER = "오늘 헤드라인 {n}개:\n{lines}\n\n위 규칙대로 JSON만 출력."


def parse_rss(xml_bytes, source=None):
    """RSS → [{title, url, source, image}]. 이미지는 media:content/thumbnail/enclosure/description의 <img> 순."""
    out = []
    for item in ET.fromstring(xml_bytes).iter("item"):
        title = re.sub(r"\s+", " ", (item.findtext("title") or "")).strip()
        link = (item.findtext("link") or "").strip()
        if not link.startswith("http") or len(title) < 8 or len(re.findall(r"[가-힣]", title)) < 4:
            continue  # 방송 코너명('클로징'), 외국어판 기사 제외
        img = None
        for e in item.iter():
            tag = e.tag.rsplit("}", 1)[-1]
            if tag in ("content", "thumbnail", "enclosure") and (e.get("url") or "").startswith("http") and "image" in (e.get("type") or "image"):
                img = e.get("url")
                break
        if not img:
            mt = re.search(r'<img[^>]+src="(https?://[^"]+)"', item.findtext("description") or "")
            img = mt.group(1) if mt else None
        src = source or (item.findtext("source") or "").strip() or None
        out.append({"title": title[:200], "url": link[:500], "source": (src or "")[:50] or None, "image": (img or "")[:500] or None})
    return out


def og_image(url):
    """기사 페이지의 og:image. 실패하면 None."""
    try:
        html = requests.get(url, timeout=6, headers=HEADERS).text[:200000]
        mt = re.search(r'property=["\']og:image["\']\s+content=["\']([^"\']+)', html) or re.search(r'content=["\']([^"\']+)["\']\s+property=["\']og:image', html)
        return mt.group(1)[:500] if mt and mt.group(1).startswith("http") else None
    except Exception:
        return None


def fetch_news(limit=30, per_feed=6):
    """언론사별 최신 per_feed건씩 라운드로빈으로 섞어서 반환 (한 매체 독식 방지)."""
    buckets = []
    for name, url in FEEDS.items():
        try:
            r = requests.get(url, timeout=10, headers=HEADERS)
            buckets.append(parse_rss(r.content, name)[:per_feed])
        except Exception:
            continue
    out, seen = [], set()
    for row in zip(*buckets) if buckets else []:
        for n in row:
            if n["title"] not in seen:
                seen.add(n["title"])
                out.append(n)
    return out[:limit]


def fetch_headlines(limit=40):
    return [n["title"] for n in fetch_news(limit)]


def parse_issues(text):
    """모델 출력(JSON, 코드펜스 섞여도 됨) → (main, [issues]). 필드 정규화·길이 제한."""
    s = text[text.find("{"): text.rfind("}") + 1]
    data = json.loads(s)

    def norm(d):
        return {"title": d["title"].strip()[:200], "summary": (d.get("summary") or "").strip(),
                "category": (d.get("category") or "").strip()[:20] or None,
                "opt_yes": (d.get("opt_yes") or "찬성").strip()[:30], "opt_no": (d.get("opt_no") or "반대").strip()[:30]}
    return norm(data["main"]), [norm(i) for i in data["issues"][:5]]


def generate(api_key, headlines=None):
    import anthropic
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY 미설정")
    headlines = headlines or fetch_headlines()
    if not headlines:
        raise RuntimeError("뉴스 헤드라인 수집 실패")
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=MODEL, max_tokens=4000, output_config={"effort": "low"}, system=SYSTEM,
        messages=[{"role": "user", "content": USER.format(n=len(headlines), lines="\n".join(f"- {h}" for h in headlines))}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("모델이 생성을 거부함")
    return parse_issues("".join(b.text for b in resp.content if b.type == "text"))
