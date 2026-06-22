# -*- coding: utf-8 -*-
"""
셀팜(sellpharm) 매물 크롤러  ─  접두사 sp_

[사이트 구조 - 실제 확인됨 2026-06-21]
  - 플랫폼 : 커스텀 PHP/Laravel 형태. 서버사이드 렌더(SSR).
  - 인코딩 : UTF-8 (https)
  - 목록(비로그인 OK) : https://sellpharm.co.kr/estate/lists  (페이지: /estate/lists/2, /3 ...)
      · 한 항목(li) 구조: .category(rental/new/sale=유형) + 지역span + 상권span + .tit(제목)
        + "월조제 X / 일매 Y" + (임대료 보증/월세 | 분양가 | 매매가) + 등록일(TODAY|YYYY-MM-DD)
      · 약 3페이지(~60건)
  - 상세 : https://sellpharm.co.kr/estate/view/<id>
      · ⚠️ 로그인 필수(비로그인 시 /login 으로 리다이렉트). 연락처·정확주소·면적·상세설명·이미지는 로그인 필요.
      · CEO 계정 미보유 → 본 크롤러는 **목록 항목만 수집**(상세 미요청).

[대시보드 필드 매핑 - build_dashboard 라벨 기준]
  - sale_count → "월조제료"  : 셀팜 '월조제' 값
  - sale_amount → "1일매출"  : 셀팜 '일매' 값
  - gubun_type : 유형(임대/매매/신규분양),  trade_area : 상권,  rent/price : 금액

[보안 설계] (팜플/약사공론/큐팜/땡큐팜과 동일 원칙)
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱(스크립트 미실행).
  - script/style/iframe/object/embed/link 등 위험 태그 제거.
  - 텍스트 제어문자 제거+길이제한, URL은 sellpharm.co.kr+http(s)만 허용.
  - 응답크기/페이지수 상한, 빈·중복 페이지 감지로 무한루프 방지.
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
BASE_URL     = "https://sellpharm.co.kr"
ALLOWED_HOST = "sellpharm.co.kr"
LIST_URL     = "https://sellpharm.co.kr/estate/lists"
PREFIX       = "sp_"
SOURCE       = "sellpharm"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_DELAY   = 1.0
MAX_EMPTY_PAGES = 2
MAX_PAGES       = 20          # 보안: 목록 페이지 수 상한
MAX_ITEMS       = 200         # 보안: 수집 최대 건수
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한

# 유형 클래스 → 한글
CAT_MAP = {"rental": "임대", "sale": "매매", "new": "신규분양", "parcel": "신규분양"}
# 상권 후보
TRADE_AREAS = ["로컬의원", "종합병원", "대형쇼핑몰", "기타"]


# ── 유틸 / 보안 살균 ─────────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: sellpharm 도메인 + http(s)만 허용."""
    if not raw:
        return ""
    u = str(raw).strip()
    if u.startswith("//"):
        u = "https:" + u
    elif u.startswith("/"):
        u = BASE_URL + u
    if not (u.startswith("https://") or u.startswith("http://")):
        return ""
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return ""
    if host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST):
        return u
    return ""


def now_kst():
    """현재 한국시간 문자열"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def today_dot():
    """오늘 날짜 (YYYY.MM.DD) - 'TODAY' 표기 치환용"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d")


def normalize_date(raw):
    """'2026-06-20' → '2026.06.20'. 'TODAY'면 오늘 날짜. 형식 아니면 ''"""
    if not raw:
        return ""
    if re.search(r"today", str(raw), re.I):
        return today_dot()
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(raw))
    if not m:
        return ""
    return f"{m.group(1)}.{m.group(2).zfill(2)}.{m.group(3).zfill(2)}"


# ── 스키마 ────────────────────────────────────────────────────────────────────

def empty_item():
    """대시보드 표준 스키마(handoff v4 §3.6). 모든 키를 빈 값으로 초기화."""
    return {
        "idx": "", "source": SOURCE, "title": "", "region": "", "location": "",
        "price": "", "rent": "", "phone": "", "owner": "", "date": "",
        "area_label": "", "area_full": "", "trade_area": "", "gubun_type": "",
        "seller_type": "", "sale_count": "", "sale_amount": "", "maintenance_fee": "",
        "building_usage": "", "approval_date": "", "floor_label": "", "rooms": "",
        "bathroom": "", "parking_label": "", "direction": "", "move_in": "",
        "thumb_url": "", "tags": [], "memo": "", "link": "",
        "status": "판매중", "collected_at": now_kst(),
    }


# ── HTTP / 보안 공통 ──────────────────────────────────────────────────────────

def fetch_html(session, url, referer=None):
    """GET → HTML 텍스트(UTF-8). 응답 크기 상한 적용. 실패 시 None."""
    try:
        h = dict(HEADERS)
        if referer:
            h["Referer"] = referer
        resp = session.get(url, headers=h, timeout=20)
        resp.raise_for_status()
        if resp.content and len(resp.content) > MAX_BYTES:
            log.warning("[셀팜] 응답 과대(%d bytes) → 절단", len(resp.content))
            return resp.content[:MAX_BYTES].decode("utf-8", "ignore")
        resp.encoding = "utf-8"
        return resp.text
    except requests.exceptions.Timeout:
        log.error("[셀팜] 타임아웃: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("[셀팜] 요청 오류: %s - %s", url, e)
    except Exception as e:
        log.error("[셀팜] 예상치 못한 오류: %s - %s", url, e)
    return None


def make_soup(html_text):
    """HTML → BeautifulSoup. 위험 태그 제거(보안)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "link", "noscript"]):
        bad.decompose()
    return soup


def list_page_url(page):
    """목록 페이지 URL (1페이지는 기본 경로)"""
    return LIST_URL if page <= 1 else "%s/%d" % (LIST_URL, page)


# ── 목록 파싱 ──────────────────────────────────────────────────────────────────

def parse_amount_block(text):
    """li 텍스트에서 금액(임대료/분양가/매매가) 추출 → (label, value)"""
    m = re.search(r"(임대료|분양가|매매가)\s*([\d,]+\s*만원(?:\s*/\s*[\d,]+\s*만원)?)", text)
    if m:
        return m.group(1), clean_text(m.group(2), 40)
    return "", ""


def parse_list(html_text):
    """목록 HTML → [item_dict] (목록 항목만)"""
    out = []
    if not html_text:
        return out
    soup = make_soup(html_text)
    seen = set()

    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.search(r"/estate/view/(\d+)", href)
        if not m:
            continue
        eid = m.group(1)
        if eid in seen:
            continue
        seen.add(eid)

        li = a.find_parent("li") or a
        item = empty_item()
        item["idx"] = PREFIX + eid
        item["link"] = safe_url("/estate/view/%s" % eid) or (BASE_URL + "/estate/view/" + eid)

        # 유형: .category 클래스(rental/new/sale) 우선, 없으면 텍스트
        cat = li.select_one(".category")
        if cat:
            cls = " ".join(cat.get("class", [])).replace("category", "").strip()
            item["gubun_type"] = CAT_MAP.get(cls.split()[0] if cls else "", clean_text(cat.get_text(), 10))
        # 제목
        tit = li.select_one(".tit")
        if tit:
            item["title"] = clean_text(tit.get_text(" ", strip=True), 120)

        # li 전체 텍스트 기반 보조 파싱
        text = clean_text(li.get_text(" ", strip=True), 400)

        # 상권
        for ta in TRADE_AREAS:
            if ta in text:
                item["trade_area"] = ta
                break

        # 지역: "시도약칭 + 시군구" (예: 경기 안산시 / 경북 상주시 / 전북특별자치도 전주시)
        rm = re.search(r"(서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)\S*\s+\S+?[시군구]", text)
        if rm:
            item["location"] = clean_text(rm.group(0), 30)
            item["region"] = rm.group(1)

        # 월조제 → sale_count(월조제료),  일매 → sale_amount(1일매출)
        jm = re.search(r"월조제\s*([^/]+?)\s*/\s*일매\s*([^\s]+(?:\s*만원)?)", text)
        if jm:
            item["sale_count"] = clean_text(jm.group(1), 20)
            item["sale_amount"] = clean_text(jm.group(2), 20)

        # 금액(임대료/분양가/매매가)
        amt_label, amt_val = parse_amount_block(text)
        if amt_label == "임대료":
            item["rent"] = amt_val
        elif amt_label in ("분양가", "매매가"):
            item["price"] = "%s %s" % (amt_label, amt_val)

        # 등록일
        dm = re.search(r"(TODAY|today|20\d{2}[-.]\d{1,2}[-.]\d{1,2})", text)
        if dm:
            item["date"] = normalize_date(dm.group(1))

        out.append(item)
    return out


# ── 메인 크롤 ──────────────────────────────────────────────────────────────────

def crawl():
    """셀팜 매물 목록 수집 → { 'sp_<id>': item_dict }.
    상세는 로그인 필수라 미수집(목록 항목만). 로그인 불필요(목록 공개).
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    empty_streak = 0
    for page in range(1, MAX_PAGES + 1):
        url = list_page_url(page)
        html_text = fetch_html(session, url, referer=LIST_URL)
        rows = parse_list(html_text)
        new = [r for r in rows if r["idx"] not in result]
        if not new:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_PAGES:
                break
        else:
            empty_streak = 0
            for r in new:
                # 공지/광고성 제외(제목 비었으면 스킵)
                if not r.get("title"):
                    continue
                result[r["idx"]] = r
        time.sleep(REQUEST_DELAY)
        if len(result) >= MAX_ITEMS:
            break

    log.info("[셀팜] 최종 %d건 수집 완료(목록 전용)", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = crawl()
    print("수집 건수:", len(data))
    for k, v in list(data.items())[:5]:
        print(k, "|", v.get("gubun_type"), "|", v.get("region"), "|", v.get("title"),
              "| 월조제:", v.get("sale_count"), "| 일매:", v.get("sale_amount"),
              "| 임대료:", v.get("rent"), "| 가격:", v.get("price"), "| 날짜:", v.get("date"))
