# -*- coding: utf-8 -*-
"""
셀팜(sellpharm) 매물 크롤러  ─  접두사 sp_

[사이트 구조 - 실제 확인됨 2026-08-21]
  - 플랫폼 : 커스텀 PHP 형태. 서버사이드 렌더(SSR). UTF-8 / https
  - 목록(비로그인 OK) : https://sellpharm.co.kr/estate/lists  (페이지: /estate/lists/2, /3 ...)
      · 한 항목(li) 구조: .category(rental/new/sale=유형) + 지역span + 상권span + .tit(제목)
        + "월조제 X / 일매 Y" + (임대료 보증/월세 | 분양가 | 매매가) + 등록일(TODAY|YYYY-MM-DD)
      · 약 3페이지(~60건)
  - 상세 : https://sellpharm.co.kr/estate/view/<id>   ★ 로그인 필수
      · 로그인 방식(실측 확정):
          POST https://sellpharm.co.kr/login/proc
          multipart/form-data 로 userid / userpw 전송 (평문, CSRF 토큰 없음)
          ★ data= 로 보내면 실패({"result":false}). 반드시 files= 로 multipart 전송.
          응답 {"result":true}=성공 / false=계정오류 / 'agree'=관리자 승인대기
      · 상세는 <table>/<dl> 없이 전부 <div> → 텍스트 라인 기반 파싱 사용
      · 매물 사진 없음. 매물의 정확한 주소도 없음(중개사무소 주소만 표시됨)
      · 페이지 하단에 이용약관/개인정보처리방침이 1만자 이상 붙으므로
        "매물 정보를 반드시 확인" 문구에서 잘라내야 함

[대시보드 필드 매핑]
  - sale_count → "월조제료"(조제료(월)),  sale_amount → "1일매출"(일반매출(일))
  - gubun_type : 거래유형,  trade_area : 상권,  rent/price : 금액

[보안 설계] (팜플/약사공론/큐팜/땡큐팜과 동일 원칙)
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱(스크립트 미실행).
  - script/style/iframe/object/embed/link 등 위험 태그 제거.
  - 텍스트 제어문자 제거+길이제한, URL은 sellpharm.co.kr+http(s)만 허용.
  - 응답크기/페이지수/건수 상한, 빈·중복 페이지 감지로 무한루프 방지.
  - 계정정보는 코드에 저장하지 않고 환경변수(GitHub Secrets) / .env 만 사용.
  - ★ 로그인 실패 시 기존 방식(목록 전용)으로 자동 폴백 → 전체 크롤링이 죽지 않음.
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
LOGIN_URL    = "https://sellpharm.co.kr/login"
LOGIN_PROC   = "https://sellpharm.co.kr/login/proc"
DETAIL_URL   = "https://sellpharm.co.kr/estate/view/%s"
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
MAX_DETAILS     = 200         # 보안: 상세 조회 최대 건수
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한

# 유형 클래스 → 한글
CAT_MAP = {"rental": "임대", "sale": "매매", "new": "신규분양", "parcel": "신규분양"}
# 상권 후보
TRADE_AREAS = ["로컬의원", "종합병원", "대형쇼핑몰", "기타"]

# ── 상세 페이지 파싱용 상수 (실측 검증 완료) ────────────────────────────────
DETAIL_LABELS = [
    "거래유형", "임대료", "매매가", "분양가", "면적", "형태", "상권",
    "관리비", "수익구조", "상세설명",
    "중개대상물 종류", "행정기간 승인일", "총층", "해당층", "방 수",
    "화장실", "총 주차대수", "주차 가능대수", "방향", "입주가능일",
]
# 값이 아니라 메뉴/구역제목이므로 건너뛸 줄
DETAIL_SKIP = {
    "건축물 정보", "매물정보", "매물등록", "매도의뢰", "마이페이지",
    "로그아웃", "로그인", "셀팜", "목록으로", "전화상담", "문자상담",
}
DETAIL_END_MARK = "매물 정보를 반드시 확인"   # 이 문구 뒤는 푸터/약관


# ── 유틸 / 보안 살균 ─────────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def clean_multiline(s, max_len=1500):
    """여러 줄 텍스트 살균: 줄바꿈은 유지하고 제어문자만 제거"""
    if not s:
        return ""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(s))
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.splitlines()]
    s = "\n".join([ln for ln in lines if ln])
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


# ── 로그인 ────────────────────────────────────────────────────────────────────

def load_env():
    """.env 로드(로컬 실행용). 실패해도 무시. GitHub Actions는 env 주입."""
    try:
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        if not os.path.exists(p):
            return
        for line in open(p, encoding="utf-8").read().splitlines():
            line = line.strip()
            if line and "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
    except Exception as e:
        log.warning("[셀팜] .env 로드 실패(무시): %s", e)


def login(session):
    """셀팜 로그인. 성공 True / 계정 미설정·실패 False(→ 목록 전용 폴백).

    실측 확정 사항:
      - multipart/form-data 로 보내야 함 (files= 사용). data= 로 보내면 result:false
      - 사전에 /login 을 GET 해서 PHPSESSID 쿠키를 먼저 받아야 함
    """
    load_env()
    uid = (os.environ.get("SELLPHARM_EMAIL", "").strip()
           or os.environ.get("SELLPHARM_ID", "").strip())
    passwd = os.environ.get("SELLPHARM_PASSWORD", "").strip()
    if not uid or not passwd:
        log.info("[셀팜] 계정정보 없음 → 비로그인(목록만 수집)")
        return False

    try:
        # 1) 로그인 페이지 GET → 세션 쿠키(PHPSESSID) 확보
        session.get(LOGIN_URL, headers=HEADERS, timeout=20)

        # 2) multipart 로 로그인 요청
        h = dict(HEADERS)
        h.update({
            "Referer": LOGIN_URL,
            "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        })
        resp = session.post(
            LOGIN_PROC,
            headers=h,
            files={"userid": (None, uid), "userpw": (None, passwd)},
            timeout=20,
        )
        body = (resp.text or "")[:300]

        # 3) 응답 판정 (main.js 기준: false=계정오류, 'agree'=승인대기, 그 외=성공)
        if '"result":false' in body.replace(" ", "") or "'result':false" in body.replace(" ", ""):
            log.warning("[셀팜] 로그인 실패: 아이디/비밀번호 오류 → 목록만 수집")
            return False
        if "agree" in body:
            log.warning("[셀팜] 로그인 실패: 관리자 승인 대기 상태 → 목록만 수집")
            return False
        if '"result":true' in body.replace(" ", "") or "result" in body:
            log.info("[셀팜] 로그인 ok=True")
            return True

        log.warning("[셀팜] 로그인 응답 해석 불가(%r) → 목록만 수집", body[:80])
        return False
    except Exception as e:
        log.error("[셀팜] 로그인 오류(무시하고 목록만 수집): %s", e)
        return False


def is_logged_in_detail(html_text):
    """상세 응답이 진짜 상세 페이지인지 판정.
    비로그인 시 /login 으로 리다이렉트되어 로그인 폼이 돌아온다."""
    if not html_text:
        return False
    if 'name="userpw"' in html_text or "id=\"userpw\"" in html_text:
        return False
    return len(html_text) > 20000


# ── 상세 페이지 파싱 (실측 데이터 3건으로 검증 완료) ────────────────────────

def parse_detail(full_text):
    """상세 페이지 전체 텍스트 → {항목명: 값} 사전"""
    out = {}
    if not full_text:
        return out

    # 1) 매물 정보 구간만 잘라내기 (하단 약관 1만자 이상 제거)
    end = full_text.find(DETAIL_END_MARK)
    body = full_text[:end] if end > 0 else full_text
    lines = [z.strip() for z in body.splitlines() if z.strip()]

    # 2) 중개업소 정보 (페이지 맨 앞 블록)
    for i, ln in enumerate(lines):
        if ln == "등록번호" and i + 1 < len(lines):
            out["중개업소"] = lines[i - 1] if i > 0 else ""
            out["등록번호"] = lines[i + 1]
        elif ln == "대표전화" and i + 1 < len(lines):
            out.setdefault("연락처", lines[i + 1])
        elif ln == "연락 가능시간" and i + 1 < len(lines):
            out.setdefault("연락가능시간", lines[i + 1])
        if "연락가능시간" in out:
            break

    # 3) 제목 / 등록일 / 지역 — 날짜 줄 기준 앞줄=제목, 뒷줄=지역
    for i, ln in enumerate(lines):
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", ln):
            out["등록일"] = ln.replace("-", ".")
            if i > 0:
                out["제목"] = lines[i - 1]
            if i + 1 < len(lines):
                out["지역"] = lines[i + 1]
            break

    # 4) 회원 구분
    for ln in lines:
        if ln in ("중개사 회원", "일반 회원", "약사 회원"):
            out["회원구분"] = ln
            break

    # 5) 라벨 기반 항목 추출 (다음 라벨이 나올 때까지 값으로 묶음)
    label_set = set(DETAIL_LABELS)
    cur = None
    buf = []
    for ln in lines:
        if ln in label_set:
            if cur and buf:
                out[cur] = "\n".join(buf).strip()
            cur = ln
            buf = []
        elif cur:
            if ln in DETAIL_SKIP:
                continue
            if ln == out.get("중개업소"):   # 중개업소 블록이 다시 나오면 본문 끝
                break
            buf.append(ln)
    if cur and buf:
        out[cur] = "\n".join(buf).strip()

    return out


def fetch_detail(session, eid):
    """상세 페이지 요청 → 파싱 결과 사전. 실패 시 None."""
    url = DETAIL_URL % eid
    html_text = fetch_html(session, url, referer=LIST_URL)
    if not is_logged_in_detail(html_text):
        return None
    soup = make_soup(html_text)
    text = soup.get_text("\n", strip=True)
    return parse_detail(text)


# ── 상세 → 대시보드 스키마 매핑 ──────────────────────────────────────────────

def enrich(item, d):
    """parse_detail 결과(d)를 대시보드 표준 필드에 채워 넣는다.
    목록에서 이미 채운 값보다 상세값을 우선한다(상세가 더 정확)."""
    if not d:
        return item

    def g(key, max_len=200):
        return clean_text(d.get(key, ""), max_len)

    # 기본 정보
    if g("제목", 120):
        item["title"] = g("제목", 120)
    if g("지역", 40):
        item["location"] = g("지역", 40)
        item["region"] = item["location"].split()[0] if item["location"] else item["region"]
    if g("등록일", 20):
        item["date"] = normalize_date(g("등록일", 20)) or item["date"]

    # 거래 조건
    if g("거래유형", 20):
        item["gubun_type"] = g("거래유형", 20)
    if g("임대료", 60):
        item["rent"] = g("임대료", 60)
    if g("매매가", 60):
        item["price"] = "매매가 " + g("매매가", 60)
    elif g("분양가", 60):
        item["price"] = "분양가 " + g("분양가", 60)

    # 면적: "전용 : 112㎡ (약 34평)" / "공급 : 112㎡ (약 34평)" 2줄
    area_raw = d.get("면적", "")
    if area_raw:
        item["area_full"] = clean_text(area_raw.replace("\n", " / "), 120)
        m = re.search(r"전용\s*:?\s*([^\n/]+)", area_raw)
        item["area_label"] = clean_text(m.group(1) if m else area_raw.splitlines()[0], 60)

    if g("상권", 30):
        item["trade_area"] = g("상권", 30)
    if g("관리비", 40):
        item["maintenance_fee"] = g("관리비", 40)

    # 수익구조: "조제료(월) 900만원" / "일반매출(일) 20만원"
    profit = d.get("수익구조", "")
    if profit:
        m = re.search(r"조제료\(월\)\s*([^\n/]+)", profit)
        if m:
            item["sale_count"] = clean_text(m.group(1), 30)
        m = re.search(r"일반매출\(일\)\s*([^\n/]+)", profit)
        if m:
            item["sale_amount"] = clean_text(m.group(1), 30)

    # 건축물 정보
    if g("중개대상물 종류", 60):
        item["building_usage"] = g("중개대상물 종류", 60)
    if g("행정기간 승인일", 60):
        item["approval_date"] = g("행정기간 승인일", 60)

    floor_now, floor_all = g("해당층", 30), g("총층", 30)
    if floor_now and floor_all:
        item["floor_label"] = "%s (총 %s)" % (floor_now, floor_all)
    elif floor_now:
        item["floor_label"] = floor_now
    elif floor_all:
        item["floor_label"] = "총 " + floor_all

    if g("방 수", 20):
        item["rooms"] = g("방 수", 20)
    if g("화장실", 30):
        item["bathroom"] = g("화장실", 30)

    park_all, park_ok = g("총 주차대수", 20), g("주차 가능대수", 20)
    if park_all and park_ok:
        item["parking_label"] = "총 %s / 가능 %s" % (park_all, park_ok)
    elif park_all or park_ok:
        item["parking_label"] = park_all or park_ok

    if g("방향", 20):
        item["direction"] = g("방향", 20)
    if g("입주가능일", 80):
        item["move_in"] = clean_text(d.get("입주가능일", "").replace("\n", " / "), 80)

    # 중개업소 정보
    if g("연락처", 30):
        item["phone"] = g("연락처", 30)
    if g("중개업소", 60):
        item["owner"] = g("중개업소", 60)
    if g("회원구분", 20):
        item["seller_type"] = g("회원구분", 20)

    # 태그: 형태(조제+매약 등)
    form_type = g("형태", 40)
    if form_type:
        tags = [t.strip() for t in re.split(r"[+,/]", form_type) if t.strip()]
        item["tags"] = list(dict.fromkeys((item.get("tags") or []) + tags))[:8]

    # 메모: 상세설명 + 표에 자리 없는 항목들을 "항목: 값" 형식으로
    memo_parts = []
    desc = d.get("상세설명", "")
    if desc:
        memo_parts.append(clean_multiline(desc, 1000))
    extra = [
        ("형태", form_type),
        ("연락가능시간", g("연락가능시간", 40)),
        ("중개업소 등록번호", g("등록번호", 40)),
    ]
    for label, val in extra:
        if val:
            memo_parts.append("%s: %s" % (label, val))
    if memo_parts:
        item["memo"] = clean_multiline("\n".join(memo_parts), 1500)

    return item


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
    """셀팜 매물 수집 → { 'sp_<id>': item_dict }.
    1) 목록 수집(비로그인 가능) → 2) 로그인 성공 시 각 매물 상세까지 수집.
    로그인 실패해도 목록 결과는 그대로 반환(폴백).
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    # ── 1단계: 목록 수집 (기존 방식 유지) ──
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

    log.info("[셀팜] 목록 %d건 수집", len(result))

    # ── 2단계: 로그인 후 상세 수집 (실패 시 목록 결과 그대로 반환) ──
    try:
        ok = login(session)
    except Exception as e:
        log.error("[셀팜] 로그인 단계 오류(폴백): %s", e)
        ok = False

    if not ok:
        log.info("[셀팜] 최종 %d건 수집 완료(목록 전용 폴백)", len(result))
        return result

    detail_ok = 0
    detail_fail = 0
    for i, (key, item) in enumerate(list(result.items())):
        if i >= MAX_DETAILS:
            break
        eid = key[len(PREFIX):]
        try:
            d = fetch_detail(session, eid)
        except Exception as e:
            log.warning("[셀팜] 상세 오류 %s: %s", eid, e)
            d = None

        if d:
            enrich(item, d)
            detail_ok += 1
        else:
            detail_fail += 1
            # 상세가 연속으로 계속 실패하면 세션이 끊긴 것 → 남은 건은 목록값 유지
            if detail_fail >= 5 and detail_ok == 0:
                log.warning("[셀팜] 상세 연속 실패 → 상세 수집 중단(목록값 유지)")
                break
        time.sleep(REQUEST_DELAY)

    log.info("[셀팜] 상세 %d건 수집 (실패 %d건)", detail_ok, detail_fail)
    log.info("[셀팜] 최종 %d건 수집 완료", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = crawl()
    print("수집 건수:", len(data))
    for k, v in list(data.items())[:5]:
        print(k, "|", v.get("gubun_type"), "|", v.get("location"), "|", v.get("title"),
              "| 월조제:", v.get("sale_count"), "| 일매:", v.get("sale_amount"),
              "| 임대료:", v.get("rent"), "| 가격:", v.get("price"),
              "| 면적:", v.get("area_label"), "| 층:", v.get("floor_label"),
              "| 연락처:", v.get("phone"), "| 날짜:", v.get("date"))
