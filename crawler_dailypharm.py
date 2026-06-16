# -*- coding: utf-8 -*-
"""
데일리팜 DP부동산(dailypharm) 약국 매물 크롤러
API:
  - 목록: GET https://realty.dailypharm.com/Main/Realty/List.html?page=N
  - 상세: https://realty.dailypharm.com/Main/Realty/View.html?ID=NNN
응답: 서버 렌더링 HTML (tr.searchList_td) - 로그인 불필요(비로그인 확인 완료)

[중요] 데일리팜은 EUC-KR 인코딩입니다. (팜플은 UTF-8 — 서로 다름)
       반드시 euc-kr 로 디코딩해야 한글이 깨지지 않습니다.

[보안 설계 — handoff_v3.md 4번 규칙]
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱한다.
    스크립트를 실행하지 않으므로 악성 JS 실행 위험이 없다.
  - 파싱 전 script/style/iframe/object/embed/link 태그를 제거한다.
  - 모든 텍스트는 제어문자 제거 + 길이 제한으로 살균한다.
  - 모든 링크/이미지 URL은 dailypharm.com 도메인 + http(s)만 허용한다.
    (javascript:, data: 등 위험 스킴 및 외부 도메인 차단)
  - 응답 크기 상한, 페이지 수 상한, 중복 페이지 감지로 무한루프/과대응답을 방지한다.
"""

import os
import re
import time
import logging
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

try:
    from bs4 import BeautifulSoup
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "beautifulsoup4", "-q"])
    from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# ── 상수 ─────────────────────────────────────────────────────────────────────
LIST_API     = "https://realty.dailypharm.com/Main/Realty/List.html"
VIEW_URL     = "https://realty.dailypharm.com/Main/Realty/View.html"
BASE_URL     = "https://realty.dailypharm.com"
ALLOWED_HOST = "dailypharm.com"          # 보안: 허용 도메인(서브도메인 포함)
SITE_ENCODING = "euc-kr"                 # 데일리팜 인코딩(중요)

# 로그인(회원 전용 PLUS 프리미엄 매물 수집용). 계정은 .env / GitHub Secrets로만 주입.
LOGIN_PAGE   = "https://www.dailypharm.com/user/login"
LOGIN_SUBMIT = "https://www.dailypharm.com/user/login/submit"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://realty.dailypharm.com/Main/Realty/List.html",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}

REQUEST_DELAY   = 0.7      # 페이지당 딜레이(초) - 서버 부하 방지
MAX_EMPTY_PAGES = 2        # 빈 페이지 연속 N회면 종료
MAX_PAGES       = 50       # 보안: 페이지 수 상한 (무한루프 방지)
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한 (5MB)


# ── 유틸리티 / 보안 살균 ──────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자(널바이트 등) 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: dailypharm.com 도메인 + http(s)만 허용. 그 외엔 빈 문자열."""
    if not raw:
        return ""
    u = str(raw).strip()
    if u.startswith("/"):
        u = BASE_URL + u
    # 보안: 위험 스킴(javascript:, data: 등) 차단
    if not (u.startswith("https://") or u.startswith("http://")):
        return ""
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return ""
    # 보안: 허용 도메인(및 서브도메인)만
    if host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST):
        return u
    return ""


def now_kst():
    """현재 한국시간 문자열"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def today_kst():
    """오늘 한국날짜 (YYYY.MM.DD)"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d")


def extract_region(location):
    """지역 문자열에서 시도(첫 단어) 추출"""
    loc = clean_text(location)
    return loc.split()[0] if loc else ""


def normalize_date(raw):
    """'26.01.01' / '2026-01-01' / '2026.06.10' → '2026.06.10'. 날짜 아니면 ''"""
    if not raw:
        return ""
    m = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:                 # 2자리 연도 → 2000년대로 보정
        y = "20" + y
    return f"{y}.{mo.zfill(2)}.{d.zfill(2)}"


def map_gubun_type(category):
    """매물유형 매핑: '약국임대'→'임대', '약국매매'→'매매', '신규/분양'→'신규분양'"""
    t = category or ""
    if "신규" in t or "분양" in t:
        return "신규분양"
    if "매매" in t:
        return "매매"
    if "임대" in t:
        return "임대"
    return clean_text(t, 10)


def map_seller_type(trade):
    """거래유형 매핑: '중개거래'→'중개매물', '약사거래'→'약사직거래', '일반거래'→''"""
    t = trade or ""
    if "중개" in t:
        return "중개매물"
    if "약사" in t:
        return "약사직거래"
    return ""


# ── 파싱 ──────────────────────────────────────────────────────────────────────

def _extract_id_and_link(card):
    """td.title 안 <a href='/Main/Realty/View.html?ID=NNN'> 에서 id·링크 추출"""
    a = card.select_one("td.title a[href*='View.html']")
    if not a:
        a = card.select_one("a[href*='View.html']")
    if not a:
        return "", ""
    href = a.get("href", "") or ""
    m = re.search(r"ID=(\d+)", href)
    item_id = m.group(1) if m else ""
    return item_id, safe_url(href)


def parse_item_card(card):
    """tr.searchList_td 카드 → 표준 스키마 dict. 실패 시 None."""
    try:
        item_id, href = _extract_id_and_link(card)
        if not item_id:
            log.debug("ID 없음 → 스킵")
            return None

        key = f"dp_{item_id}"

        # 매물유형(임대/매매) — td.category 안 span.cont_category
        cat_el     = card.select_one("td.category .cont_category") or card.select_one("td.category")
        category   = clean_text(cat_el.get_text(), 20) if cat_el else ""
        gubun_type = map_gubun_type(category)

        # 거래유형(일반/중개/약사) — td.type
        type_el     = card.select_one("td.type")
        trade       = clean_text(type_el.get_text(), 20) if type_el else ""
        seller_type = map_seller_type(trade)

        # 제목 + 지역 — td.title 안에 <span><a>제목</a></span><span>지역</span>
        title    = ""
        location = ""
        title_td = card.select_one("td.title")
        if title_td:
            a = title_td.select_one("a")
            if a:
                title = clean_text(a.get_text(), 200)
            spans = title_td.find_all("span", recursive=False)
            if len(spans) >= 2:
                location = clean_text(spans[-1].get_text(), 100)
            elif spans:
                # span이 1개뿐이고 제목 a를 뺀 나머지 텍스트가 지역일 수 있음
                rest = title_td.get_text()
                if title:
                    rest = rest.replace(title, "", 1)
                location = clean_text(rest, 100)
        if not title:
            title = f"데일리팜 매물 #{item_id}"

        region = extract_region(location)

        # 면적 — td.area ("71.89 m2 (21.75평)")
        area_el   = card.select_one("td.area")
        area_full = clean_text(area_el.get_text(), 60) if area_el else ""

        # 등록일 — td.date
        date_el  = card.select_one("td.date")
        date_str = normalize_date(date_el.get_text()) if date_el else ""

        # 태그
        tags = []
        if seller_type:
            tags.append(seller_type)
        if gubun_type:
            tags.append(gubun_type)

        # 메모
        memo = []
        if location:    memo.append(f"지역: {location}")
        if area_full:   memo.append(f"면적: {area_full}")
        if gubun_type:  memo.append(f"유형: {gubun_type}")
        if trade:       memo.append(f"거래: {trade}")
        if href:        memo.append(f"링크: {href}")

        return {
            "idx":          key,
            "dailypharm_id": item_id,
            "source":       "dailypharm",
            "title":        title,
            "region":       region,
            "location":     location,
            "price":        "",          # 목록에 가격 미노출(상세에서만)
            "rent":         "",
            "phone":        "",          # 목록 비공개
            "owner":        "",
            "date":         date_str,
            "area_label":   "",
            "area_full":    area_full,
            "trade_area":   "",
            "gubun_type":   gubun_type,
            "seller_type":  seller_type,   # 중개매물 / 약사직거래 / ""
            "sale_count":   "",          # 월 조제 — 목록 미노출
            "sale_amount":  "",
            "daily_sales":  "",
            "thumb_url":    "",          # 목록 썸네일 없음
            "tags":         ", ".join(dict.fromkeys(tags)),
            "memo":         "\n".join(memo),
            "special_flag": "",
            "status":       "active",
            "collected_at": now_kst(),
            "link":         href,
        }
    except Exception as e:
        log.warning("parse_item_card 오류: %s", e, exc_info=True)
        return None


def parse_html_response(html_text):
    """목록 HTML → 매물 카드 dict 리스트 (보안 살균 포함)"""
    items = []
    if not html_text or not html_text.strip():
        return items
    soup = BeautifulSoup(html_text, "html.parser")
    # 보안: 실행 가능/위험 요소 제거
    for bad in soup(["script", "style", "iframe", "object", "embed", "link"]):
        bad.decompose()
    for card in soup.select("tr.searchList_td"):
        parsed = parse_item_card(card)
        if parsed:
            items.append(parsed)
    return items


# ── API 호출 ──────────────────────────────────────────────────────────────────

def fetch_page(session, page):
    """GET 요청 → EUC-KR 디코딩한 HTML 텍스트. 실패 시 None. (응답 크기 상한 적용)"""
    # TypeView=P: PLUS(프리미엄 포함) 전체 목록. keyword 빈값 (사이트와 동일 형식)
    params = {"TypeView": "P", "page": str(page), "keyword": ""}
    try:
        resp = session.get(LIST_API, params=params, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        raw = resp.content or b""
        # 보안: 과대 응답 차단
        if len(raw) > MAX_BYTES:
            log.warning("응답이 너무 큼(%d bytes) → 절단", len(raw))
            raw = raw[:MAX_BYTES]
        # 중요: 데일리팜은 EUC-KR
        return raw.decode(SITE_ENCODING, "ignore")
    except requests.exceptions.Timeout:
        log.error("타임아웃: 데일리팜 p%d", page)
    except requests.exceptions.RequestException as e:
        log.error("요청 오류: 데일리팜 p%d - %s", page, e)
    except Exception as e:
        log.error("예상치 못한 오류: 데일리팜 p%d - %s", page, e)
    return None


def load_env():
    """.env 파일에서 환경변수 로드 (로컬 실행용; GitHub Actions는 env로 주입). 실패해도 무시."""
    try:
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if not os.path.exists(env_path):
            return
        for line in open(env_path, encoding="utf-8").read().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except Exception as e:
        log.warning("[데일리팜] .env 로드 실패(무시): %s", e)


def login(session):
    """데일리팜 로그인 시도.
    - DAILYPHARM_EMAIL(또는 DAILYPHARM_ID) / DAILYPHARM_PASSWORD 가 있으면 로그인 → 세션에 인증쿠키.
    - 계정이 없거나 실패하면 False 반환 → 비로그인(공개 매물 약 12건)으로 안전하게 진행.
    - 비밀번호는 절대 로그로 출력하지 않는다(마스킹).
    """
    load_env()
    member_id = (os.environ.get("DAILYPHARM_EMAIL", "").strip()
                 or os.environ.get("DAILYPHARM_ID", "").strip())
    password  = os.environ.get("DAILYPHARM_PASSWORD", "").strip()
    if not member_id or not password:
        log.info("[데일리팜] 계정정보 없음 → 비로그인 수집(공개 매물만, 약 12건)")
        return False
    try:
        # 1) 로그인 페이지 GET → CSRF 토큰(_token) + 세션쿠키 확보
        r = session.get(LOGIN_PAGE, headers=HEADERS, timeout=20)
        r.raise_for_status()
        m = re.search(r'name=["\']_token["\'][^>]*value=["\']([^"\']+)["\']', r.text) \
            or re.search(r'value=["\']([^"\']+)["\'][^>]*name=["\']_token["\']', r.text)
        if not m:
            log.warning("[데일리팜] CSRF 토큰 못 찾음 → 비로그인 진행")
            return False
        token = m.group(1)
        # 2) 로그인 제출 (member_id / password / _token)
        post_headers = dict(HEADERS)
        post_headers["Content-Type"] = "application/x-www-form-urlencoded"
        post_headers["Origin"]  = "https://www.dailypharm.com"
        post_headers["Referer"] = LOGIN_PAGE
        data = {"_token": token, "member_id": member_id, "password": password, "remember": "on"}
        resp = session.post(LOGIN_SUBMIT, data=data, headers=post_headers,
                            timeout=20, allow_redirects=True)
        # 3) 성공 판정: 로그아웃 노출 또는 로그인 페이지로 되돌아가지 않음 (비밀번호는 로그 금지)
        body = resp.text or ""
        ok = ("로그아웃" in body) or ("logout" in body.lower()) \
             or (bool(resp.url) and "login" not in str(resp.url).lower())
        if ok:
            log.info("[데일리팜] 로그인 성공 → 프리미엄 포함 전체 수집")
            return True
        log.warning("[데일리팜] 로그인 실패(자격증명 확인 필요) → 비로그인 진행")
        return False
    except Exception as e:
        log.error("[데일리팜] 로그인 중 오류 → 비로그인 진행: %s", e)
        return False


def crawl():
    """데일리팜 약국 매물 전체 수집 → {dp_id: item} dict

    [매우 중요] 데일리팜 목록 페이저는 **0부터 시작**한다.
      화면의 "1페이지" = page=0, "2페이지" = page=1 ...
      (1부터 돌리면 첫 페이지 page=0 을 통째로 건너뛰어 매물 절반을 놓친다 — 과거 버그)
    page=0,1,2... 순차로 돌며 빈 페이지가 MAX_EMPTY_PAGES회 연속이면 종료.
    로그인은 필수 아님(비로그인도 전체 수집됨). 단, login()은 무해하게 유지(향후 대비).
    """
    session = requests.Session()
    session.headers.update({"User-Agent": HEADERS["User-Agent"],
                            "Referer": HEADERS["Referer"]})
    logged_in = login(session)  # 선택적: 계정 있으면 로그인, 없으면 비로그인(둘 다 전체 수집)
    log.info("[데일리팜] 로그인 상태: %s", "로그인" if logged_in else "비로그인")
    items = {}
    empty = 0
    log.info("[데일리팜] 수집 시작 - %s", LIST_API)
    page = 0  # ★ 0부터 시작 (데일리팜 페이저 0-인덱스)
    while page < MAX_PAGES:
        html_text = fetch_page(session, page)
        if html_text is None:
            log.warning("[데일리팜] page=%d 응답 없음 → 종료", page)
            break
        page_items = parse_html_response(html_text)
        new = 0
        for it in page_items:
            if it["idx"] not in items:
                items[it["idx"]] = it
                new += 1
        if not page_items:
            empty += 1
            log.info("[데일리팜] page=%d 빈 페이지 (%d/%d)", page, empty, MAX_EMPTY_PAGES)
            if empty >= MAX_EMPTY_PAGES:
                break
        else:
            empty = 0
            log.info("[데일리팜] page=%d 신규 %d건 (누적 %d건)", page, new, len(items))
        page += 1
        time.sleep(REQUEST_DELAY)

    log.info("데일리팜 크롤링 완료: 총 %d건", len(items))
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = crawl()
    print(f"\n총 {len(result)}건")
    for k, v in list(result.items())[:5]:
        print(f"[{k}] {v['title']} | {v['location']} | {v['gubun_type']} | {v['seller_type']} | {v['date']}")
