# -*- coding: utf-8 -*-
"""
약준모(yakjunmo) 매물 크롤러  ─  접두사 yj_

[사이트 구조 - 실제 확인됨 2026-06-22]
  - 플랫폼 : Rhymix (XE 후속 CMS). 서버사이드 렌더(SSR). https, UTF-8.
  - 목록(비로그인 OK) : https://recruit.pharmmaker.com/index.php?mid=sale&page=N  (약 34페이지)
      · 분류(카테고리) : 약국매도=category/1811(매물), 약국매수=category/1812(제외)
      · 목록 컬럼 : 번호, 분류, 제목(→/sale/<num>), 글쓴이(닉네임), 날짜, 조회수
  - 상세(비로그인 OK) : https://recruit.pharmmaker.com/sale/<num>
      · 공개 표 : 지역, 주처방과, 평일근무시간, 약국평수, 약국구분, 진행상황
      · 가려진 표 : 월조제료/매출/보증금/월세/권리금 → "약사회원만 확인가능합니다."
      · ★메타태그 우회 : <meta property="og:description"> 에 본문 전체가 비로그인에도 노출.
        → 본문에서 일매출/보증금/월세/권리금/전화 정규식 추출(판매자가 본문에 적은 경우).
      · ⚠️ 한계 : og:description 약 150~200자에서 잘림(긴 본문 뒷부분 누락 가능),
                 표 칸만 채우고 본문 미기재 시 수치 못 얻음.

[대시보드 필드 매핑 - build_dashboard 라벨 기준]
  - sale_amount → "1일매출"   : 본문 '일매출/월매출' 값
  - sale_count  → "월조제료"  : 표 '월조제료'(보통 가려짐) 또는 본문
  - rent        : "보증금 X / 월세 Y" (보증금+월세 결합 표기)
  - price       : 희망권리금
  - floor_label : 약국구분(예 1층약국),  area_label : 약국평수(예 70평)
  - building_usage : 주처방과,  status : 진행상황(진행중→판매중 / 완료→거래완료)

[보안 설계] (팜플/약사공론/큐팜/땡큐팜/셀팜과 동일 원칙)
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱(스크립트 미실행).
  - script/style/iframe/object/embed/link 등 위험 태그 제거.
  - 텍스트 제어문자 제거+길이제한, URL은 recruit.pharmmaker.com+http(s)만 허용.
  - 응답크기/페이지수/건수 상한, 빈·중복 페이지 감지로 무한루프 방지.
"""

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
BASE_URL     = "https://recruit.pharmmaker.com"
ALLOWED_HOST = "recruit.pharmmaker.com"
LIST_URL     = "https://recruit.pharmmaker.com/index.php?mid=sale"
PREFIX       = "yj_"
SOURCE       = "yakjunmo"

# 약국매도(매물)만 수집. 약국매수는 제외.
CAT_SELL = "1811"   # 약국매도
CAT_BUY  = "1812"   # 약국매수(제외)

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
MAX_PAGES       = 40          # 보안: 목록 페이지 수 상한(실제 ~34)
MAX_ITEMS       = 700         # 보안: 수집 최대 건수
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한

# 가려진 값 표식(약사회원 전용)
LOCKED_MARK = "약사회원만"

# 시도 약칭(지역 추출용)
SIDO = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종",
        "경기", "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]


# ── 유틸 / 보안 살균 ─────────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = str(s).replace("&nbsp;", " ")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: 약준모 도메인 + http(s)만 허용."""
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


def normalize_date(raw):
    """'26.06.16' 또는 '2026-06-16' → '2026.06.16'. 'HH:MM'(오늘 등록)이면 오늘 날짜."""
    if not raw:
        return ""
    raw = str(raw).strip()
    # 시간만 표시되면(예 11:24) 오늘 등록 → 오늘 날짜
    if re.fullmatch(r"\d{1,2}:\d{2}", raw):
        return datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d")
    # YY.MM.DD (예 26.06.16)
    m = re.search(r"\b(\d{2})[.\-/](\d{1,2})[.\-/](\d{1,2})\b", raw)
    if m:
        return f"20{m.group(1)}.{m.group(2).zfill(2)}.{m.group(3).zfill(2)}"
    # YYYY.MM.DD
    m = re.search(r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if m:
        return f"{m.group(1)}.{m.group(2).zfill(2)}.{m.group(3).zfill(2)}"
    return ""


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
            log.warning("[약준모] 응답 과대(%d bytes) → 절단", len(resp.content))
            return resp.content[:MAX_BYTES].decode("utf-8", "ignore")
        resp.encoding = "utf-8"
        return resp.text
    except requests.exceptions.Timeout:
        log.error("[약준모] 타임아웃: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("[약준모] 요청 오류: %s - %s", url, e)
    except Exception as e:
        log.error("[약준모] 예상치 못한 오류: %s - %s", url, e)
    return None


def make_soup(html_text):
    """HTML → BeautifulSoup. 위험 태그 제거(보안)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "link", "noscript"]):
        bad.decompose()
    return soup


def list_page_url(page):
    """목록 페이지 URL"""
    return "%s&page=%d" % (LIST_URL, max(1, page))


# ── 본문(og:description) 추출 정규식 ─────────────────────────────────────────

def get_og_description(html_text):
    """<meta property="og:description"> 본문 텍스트. 위험태그 제거 전 원본에서 추출."""
    if not html_text:
        return ""
    m = re.search(
        r'<meta[^>]+property=["\']og:description["\'][^>]*content=["\'](.*?)["\']',
        html_text, re.I | re.S)
    if not m:
        m = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]*content=["\'](.*?)["\']',
            html_text, re.I | re.S)
    return clean_text(m.group(1), 400) if m else ""


def extract_phone(text):
    """본문에서 휴대폰 번호 추출(공백/구분자 허용)."""
    if not text:
        return ""
    m = re.search(r"01[016789][)\-.\s]*\d{3,4}[\-.\s]*\d{4}", text)
    if not m:
        return ""
    digits = re.sub(r"\D", "", m.group(0))
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    return clean_text(m.group(0), 20)


def _won(num_text):
    """'3,000' → '3000만원' 형태 표기."""
    n = re.sub(r"[^\d]", "", num_text)
    return f"{n}만원" if n else ""


def extract_money(body):
    """본문에서 일매출/월매출, 보증금, 월세, 권리금 추출 → dict."""
    out = {"sale_amount": "", "sale_label": "", "deposit": "", "rent": "", "premium": ""}
    if not body:
        return out
    # 일매출/월매출
    m = re.search(r"(일|월)\s*매출[^\d]{0,8}([\d,]+)\s*만", body)
    if m:
        out["sale_label"] = m.group(1) + "매출"
        out["sale_amount"] = _won(m.group(2))
    # 보증금
    m = re.search(r"보증(?:금)?[^\d]{0,8}([\d,]+)\s*만", body)
    if m:
        out["deposit"] = _won(m.group(1))
    # 월세
    m = re.search(r"월\s*세[^\d]{0,8}([\d,]+)\s*만", body)
    if m:
        out["rent"] = _won(m.group(1))
    # 권리금
    m = re.search(r"권리금[^\d]{0,8}([\d,]+)\s*만", body)
    if m:
        out["premium"] = _won(m.group(1))
    return out


# ── 상세 표 파싱 ──────────────────────────────────────────────────────────────

def _val(v):
    """표 값 살균. '약사회원만 확인가능합니다.'(가려짐)는 빈 값 처리."""
    v = clean_text(v, 60)
    if not v or LOCKED_MARK in v:
        return ""
    return v


def parse_detail_table(soup):
    """상세 공개 표(label→value) dict. 가려진 값은 제외."""
    info = {}
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        # 한 행에 (라벨, 값) 쌍이 1개 이상 들어있는 구조 대응
        i = 0
        while i + 1 < len(cells):
            label = clean_text(cells[i].get_text(" ", strip=True), 30)
            value = cells[i + 1].get_text(" ", strip=True)
            if label:
                info[label] = value
            i += 2
    return info


def find_label(info, *keywords):
    """info dict에서 키워드를 포함하는 라벨의 값 반환(없으면 '')."""
    for label, value in info.items():
        if any(k in label for k in keywords):
            return value
    return ""


def parse_detail(html_text, item):
    """상세 HTML → item 보강(공개 표 + og:description 메타파싱)."""
    if not html_text:
        return item
    body = get_og_description(html_text)        # 위험태그 제거 전 원본에서
    soup = make_soup(html_text)
    info = parse_detail_table(soup)

    # 공개 표 항목
    region_v = _val(find_label(info, "지역"))
    if region_v and not item.get("region"):
        item["region"] = region_v
        if not item.get("location"):
            item["location"] = region_v
    area_v = _val(find_label(info, "평수"))
    if area_v:
        item["area_label"] = area_v
    gubun_v = _val(find_label(info, "약국구분"))
    if gubun_v:
        item["floor_label"] = gubun_v
    dept_v = _val(find_label(info, "주처방"))
    if dept_v:
        item["building_usage"] = dept_v
    work_v = _val(find_label(info, "근무시간"))
    status_v = _val(find_label(info, "진행상황"))
    if status_v:
        item["status"] = "거래완료" if ("완료" in status_v) else "판매중"

    # 표의 공개 수치(보통 가려져 있음 → 있으면 사용)
    t_sale = _val(find_label(info, "매출"))
    t_jo   = _val(find_label(info, "월조제"))
    t_dep  = _val(find_label(info, "보증금"))
    t_rent = _val(find_label(info, "월세"))
    t_prem = _val(find_label(info, "권리금"))
    if t_jo:
        item["sale_count"] = t_jo

    # 본문(메타) 수치 추출 → 표 공개값 우선, 없으면 본문값
    money = extract_money(body)
    sale_amount = t_sale or money["sale_amount"]
    if sale_amount:
        item["sale_amount"] = sale_amount
    deposit = t_dep or money["deposit"]
    rent    = t_rent or money["rent"]
    if deposit and rent:
        item["rent"] = "보증금 %s / 월세 %s" % (deposit, rent)
    elif deposit:
        item["rent"] = "보증금 %s" % deposit
    elif rent:
        item["rent"] = "월세 %s" % rent
    premium = t_prem or money["premium"]
    if premium:
        item["price"] = "권리금 %s" % premium

    # 전화(본문에 적힌 경우만)
    phone = extract_phone(body)
    if phone:
        item["phone"] = phone

    # 메모: 근무시간 + 본문 발췌
    memo_parts = []
    if work_v:
        memo_parts.append("근무 " + work_v)
    if body:
        memo_parts.append(body)
    item["memo"] = clean_text(" | ".join(memo_parts), 300)
    return item


# ── 목록 파싱 ──────────────────────────────────────────────────────────────────

def parse_list(html_text):
    """목록 HTML → [(num, item_dict)] (약국매도만, 매수 제외)."""
    out = []
    if not html_text:
        return out
    soup = make_soup(html_text)
    seen = set()

    for tr in soup.find_all("tr"):
        # 제목 링크: 실제 목록은 index.php?...document_srl=<num> 형식.
        #   (정규화 표기 /sale/<num> 도 함께 허용. 카테고리 링크는 제외.)
        title_a = None
        for a in tr.find_all("a", href=True):
            href = a["href"]
            if "category" in href:
                continue
            if re.search(r"document_srl=(\d+)", href) or re.search(r"/sale/(\d+)(?:$|[?#])", href):
                title_a = a
                break
        if not title_a:
            continue
        m = re.search(r"document_srl=(\d+)", title_a["href"]) or re.search(r"/sale/(\d+)", title_a["href"])
        if not m:
            continue
        num = m.group(1)
        if num in seen:
            continue

        # 분류: 약국매수(1812)면 제외
        row_text = clean_text(tr.get_text(" ", strip=True), 400)
        is_buy = any("category/%s" % CAT_BUY in a.get("href", "") for a in tr.find_all("a", href=True))
        if is_buy or "약국매수" in row_text:
            continue

        seen.add(num)
        item = empty_item()
        item["idx"] = PREFIX + num
        item["link"] = safe_url("/sale/%s" % num) or (BASE_URL + "/sale/" + num)
        item["gubun_type"] = "매매"
        item["title"] = clean_text(title_a.get_text(" ", strip=True), 120)

        # 제목 앞 [약국매도][경북] 형태에서 지역 보조 추출
        for sd in SIDO:
            if sd in item["title"] or sd in row_text:
                item["region"] = sd
                break

        # 날짜: HH:MM 또는 YY.MM.DD
        dm = re.search(r"\b(\d{2}[.\-/]\d{1,2}[.\-/]\d{1,2}|\d{1,2}:\d{2})\b", row_text)
        if dm:
            item["date"] = normalize_date(dm.group(1))

        out.append((num, item))
    return out


# ── 메인 크롤 ──────────────────────────────────────────────────────────────────

def crawl():
    """약준모 매물 수집 → { 'yj_<num>': item_dict }.
    목록(공개) + 상세(공개 표 + og:description 메타파싱). 로그인 불필요.
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    empty_streak = 0
    for page in range(1, MAX_PAGES + 1):
        url = list_page_url(page)
        html_text = fetch_html(session, url, referer=LIST_URL)
        rows = parse_list(html_text)
        new = [(num, it) for (num, it) in rows if it["idx"] not in result]
        if not new:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_PAGES:
                break
        else:
            empty_streak = 0
            for num, it in new:
                if not it.get("title"):
                    continue
                # 상세 보강(공개 표 + 메타). 실패해도 목록 항목은 유지.
                try:
                    detail_html = fetch_html(session, it["link"], referer=url)
                    it = parse_detail(detail_html, it)
                except Exception as e:
                    log.error("[약준모] 상세 파싱 실패(%s): %s", num, e)
                result[it["idx"]] = it
                time.sleep(REQUEST_DELAY)
                if len(result) >= MAX_ITEMS:
                    break
        time.sleep(REQUEST_DELAY)
        if len(result) >= MAX_ITEMS:
            break

    log.info("[약준모] 최종 %d건 수집 완료(목록+메타)", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = crawl()
    print("수집 건수:", len(data))
    for k, v in list(data.items())[:5]:
        print(k, "|", v.get("region"), "|", v.get("title"),
              "| 일매:", v.get("sale_amount"), "| 임대:", v.get("rent"),
              "| 권리:", v.get("price"), "| 전화:", v.get("phone"), "| 날짜:", v.get("date"))
