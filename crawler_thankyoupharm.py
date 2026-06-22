# -*- coding: utf-8 -*-
"""
땡큐팜(thankyoupharm) 매물 크롤러  ─  접두사 tq_

[사이트 구조 - 실제 확인됨 2026-06-21]
  - 플랫폼 : 전통적 PHP 게시판(그누보드형). 매물 = 게시판 글.
  - 인코딩 : UTF-8 (HTTP, http:// 사이트)
  - 목록   : http://thankyoupharm.co.kr/bbs/board.php?tbl=bbs41&page=N
             · bbs41    = 약국내놔요(매매/임대 매물)  ← 주력
             · bbs41_3  = 신규분양(신축/기존건물)
             · bbs41_2  = 약국구해요(구하는 글) → 매물 아님, 수집 제외
             · 목록은 비로그인으로 열람 가능(구분/제목/지역/진료과/조제료/작성자/날짜)
  - 상세   : http://thankyoupharm.co.kr/bbs/board.php?tbl=bbs41&mode=VIEW&num=<글번호>
             · ⚠️ 상세 본문(.con01)은 로그인 필수. 비로그인 시 메뉴만 반환(본문 없음).
             · 상세 전용 핵심 항목 = 연락처(전화). 그 외(제목/지역/진료과/조제료/날짜)는 목록에서 확보.
  - 로그인 : POST /member/login.php  (필드: mb_id, mb_pass, mode=Login, URL, PHPSESSID)
             · 회원 승인제 사이트. 승인된 계정만 상세 열람 가능.

[비로그인 폴백 설계 - 큐팜과의 차이]
  - 큐팜은 목록 자체가 로그인 차단 → 로그인 실패 시 0건.
  - 땡큐팜은 목록이 공개 → 로그인 실패해도 목록 기반 데이터는 수집(연락처만 빠짐).
  - 따라서 crawl()은 로그인 실패해도 목록을 수집하고, 로그인 성공 시에만 상세(연락처)를 보강한다.

[보안 설계] (팜플/약사공론/큐팜과 동일 원칙)
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱(스크립트 미실행).
  - script/style/iframe/object/embed/link 등 위험 태그 제거.
  - 모든 텍스트는 제어문자 제거 + 길이 제한으로 살균.
  - 모든 URL은 thankyoupharm.co.kr 도메인 + http(s)만 허용(javascript:, data: 차단).
  - 응답 크기 상한 / 페이지 수 상한 / 빈·중복 페이지 감지로 무한루프 방지.

[로그인 정보]
  - GitHub Secrets: THANKYOUPHARM_ID / THANKYOUPHARM_PASSWORD (CEO가 등록).
  - 미설정 시 비로그인 폴백(목록만 수집).
"""

import os
import re
import time
import json
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
BASE_URL     = "http://thankyoupharm.co.kr"
ALLOWED_HOST = "thankyoupharm.co.kr"                        # 보안: 허용 도메인
LOGIN_PROC   = "http://thankyoupharm.co.kr/member/login.php"  # 로그인 처리(POST)
PREFIX       = "tq_"                                        # 대시보드 출처 접두사
SOURCE       = "thankyoupharm"

# 수집 대상 게시판: (tbl, 기본거래유형 힌트)
BOARDS = [
    ("bbs41",   ""),     # 약국내놔요(매매/임대) - 주력
    ("bbs41_3", "분양"),  # 신규분양
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

REQUEST_DELAY   = 1.0          # 페이지/상세당 딜레이(초) - 서버 부하 방지
MAX_EMPTY_PAGES = 2           # 빈/중복 페이지 연속 N회면 종료
MAX_PAGES       = 30          # 보안: 목록 페이지 수 상한
MAX_DETAIL      = 200         # 보안: 상세 수집 최대 건수 상한
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한(5MB)

# 시도(광역) 명칭 - 지역 추출용
REGIONS = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시", "광주광역시",
    "대전광역시", "울산광역시", "세종특별자치시", "세종시",
    "경기도", "강원도", "강원특별자치도", "충청북도", "충청남도",
    "전라북도", "전북특별자치도", "전라남도", "경상북도", "경상남도",
    "제주특별자치도", "제주도",
]


# ── 유틸리티 / 보안 살균 ──────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)   # 제어문자 제거
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def clean_multiline(s, max_len=2000):
    """상세설명 본문용: 줄바꿈은 살리되 제어문자/과도공백만 정리 + 남은 태그 제거"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"<[^>]+>", " ", s)          # 남은 HTML 태그 잔재 제거(콘솔 에러 방지)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: thankyoupharm 도메인 + http(s)만 허용. 그 외엔 빈 문자열."""
    if not raw:
        return ""
    u = str(raw).strip()
    if u.startswith("//"):
        u = "http:" + u
    elif u.startswith("/"):
        u = BASE_URL + u
    if not (u.startswith("https://") or u.startswith("http://")):
        return ""   # 보안: javascript:, data: 등 위험 스킴 차단
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return ""
    if host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST):
        return u
    return ""


def safe_img_url(raw):
    """매물 이미지 URL 살균: 실제 매물사진만 허용.
    로고/배너/공통 디자인 이미지는 제외(깨진 이미지 방지). thankyoupharm.co.kr 업로드 경로만."""
    if not raw:
        return ""
    u = str(raw).strip()
    if u.startswith("//"):
        u = "http:" + u
    elif u.startswith("/"):
        u = BASE_URL + u
    if not (u.startswith("https://") or u.startswith("http://")):
        return ""
    low = u.lower()
    # 사이트 공통 디자인/배너/로고 이미지 = 매물사진 아님
    if any(x in low for x in ("/images/", "rolling_banner", "logo", "noimage",
                              "btn_", "/skin/", "icon", "banner")):
        return ""
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return ""
    if host == ALLOWED_HOST or host.endswith("." + ALLOWED_HOST):
        # 업로드/데이터/첨부 경로만 실제 매물사진으로 인정
        if any(x in low for x in ("/data/", "/upload", "/bbs/", "/file", "editor")):
            return u
    return ""


def now_kst():
    """현재 한국시간 문자열"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def extract_region(text):
    """문자열에서 시도(광역) 명칭 추출. 없으면 첫 단어."""
    t = clean_text(text)
    if not t:
        return ""
    for r in REGIONS:
        if r in t:
            return r
    return t.split()[0] if t else ""


def normalize_date(raw):
    """'26.01.01' / '2026-01-01' → '2026.01.01'. 날짜 형식 아니면 ''"""
    if not raw:
        return ""
    m = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", str(raw))
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:
        y = "20" + y
    return f"{y}.{mo.zfill(2)}.{d.zfill(2)}"


def write_login_status(ok, message, id_set):
    """로그인 진단 파일 기록(비밀번호 절대 미기록) → 원인 파악용"""
    try:
        _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(_dir, exist_ok=True)
        with open(os.path.join(_dir, "thankyoupharm_login_status.json"), "w", encoding="utf-8") as f:
            json.dump({"ok": bool(ok), "message": str(message)[:200],
                       "id_set": bool(id_set), "ts": now_kst()}, f, ensure_ascii=False)
    except Exception as e:
        log.warning("[땡큐팜] 진단파일 기록 실패: %s", e)


def load_env():
    """.env 로드(로컬용). 실패해도 무시. GitHub Actions는 env 주입."""
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
        log.warning("[땡큐팜] .env 로드 실패(무시): %s", e)


# ── 로그인 ────────────────────────────────────────────────────────────────────

def login(session):
    """그누보드 로그인(평문 폼 POST). 성공 True / 미설정·실패 False(비로그인 → 목록만 수집)."""
    load_env()
    uid = (os.environ.get("THANKYOUPHARM_ID", "").strip()
           or os.environ.get("THANKYOUPHARM_EMAIL", "").strip())
    passwd = os.environ.get("THANKYOUPHARM_PASSWORD", "").strip()
    if not uid or not passwd:
        log.info("[땡큐팜] 계정정보 없음 → 비로그인(목록만 수집, 연락처 제외)")
        write_login_status(False, "no_credentials", False)
        return False
    try:
        # 1) 메인 GET 으로 세션 쿠키(PHPSESSID) 확보
        session.get(BASE_URL + "/main.php", headers=HEADERS, timeout=20)
        # 2) 로그인 폼 POST (실제 폼 필드명 그대로)
        data = {
            "PHPSESSID": "", "URL": "", "mode": "Login",
            "mb_id": uid, "mb_pass": passwd,
        }
        h = dict(HEADERS)
        h.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LOGIN_PROC, "Origin": BASE_URL,
        })
        session.post(LOGIN_PROC, data=data, headers=h, timeout=20, allow_redirects=True)
        # 성공 판정: 상세 글을 받아 본문(.con01) 노출 + 로그인 폼 부재로 확인
        ok = False
        try:
            test_url = "%s/bbs/board.php?tbl=bbs41&mode=VIEW&num=235" % BASE_URL
            body = session.get(test_url, headers=HEADERS, timeout=20).text or ""
            has_login_form = ("mb_pass" in body) and ('name="login"' in body or "name='login'" in body)
            has_content = ("con01" in body) and (len(body) > 8000)
            ok = has_content and not has_login_form
        except Exception:
            pass
        write_login_status(ok, "len_check", True)
        log.info("[땡큐팜] 로그인 ok=%s", ok)
        return ok
    except Exception as e:
        log.error("[땡큐팜] 로그인 오류 → 비로그인 진행: %s", e)
        write_login_status(False, "exception:%s" % e, True)
        return False


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


# ── 한글 라벨 매핑 (약사공론 방식: 라벨 텍스트 기준) ──────────────────────────
LABEL_MAP = [
    ("phone",           ["전화", "연락처", "휴대", "핸드폰", "tel", "전화번호", "문의"]),
    ("price",           ["매매", "권리금", "매매가", "양도금", "매도금", "매도가", "분양가격", "분양가"]),
    ("rent",            ["임대", "보증금", "월세", "임대료", "보증/월세"]),
    ("maintenance_fee", ["관리비"]),
    ("area_label",      ["면적", "전용면적", "전용", "평수", "분양면적", "계약면적"]),
    ("location",        ["위치", "주소", "소재지", "지역"]),
    ("trade_area",      ["상권", "입지"]),
    ("gubun_type",      ["구분", "매물종류", "거래구분", "거래유형"]),
    ("seller_type",     ["등록자", "구분자", "회원구분"]),
    ("sale_count",      ["처방", "처방전", "일평균", "조제건수", "처방건수"]),
    ("sale_amount",     ["조제료", "조제", "매출", "일매출", "월매출", "수익", "순익", "연매출"]),
    ("building_usage",  ["건축물", "용도", "건물용도", "진료과", "병원"]),
    ("floor_label",     ["층수", "해당층"]),
    ("parking_label",   ["주차"]),
    ("date",            ["등록일", "작성일", "작성시간", "게시일", "날짜"]),
    ("owner",           ["담당", "담당자", "글쓴이", "작성자"]),
]


def map_label(field_dict, label, value):
    """라벨 텍스트를 키워드로 판별하여 적절한 스키마 필드에 채운다(이미 차 있으면 보존)."""
    lab = clean_text(label, 30).lower().replace(" ", "")
    val = clean_text(value, 200)
    if not lab or not val:
        return
    for field, keys in LABEL_MAP:
        for k in keys:
            if k.lower().replace(" ", "") in lab:
                if not field_dict.get(field):
                    field_dict[field] = val
                if field == "area_label" and not field_dict.get("area_full"):
                    field_dict["area_full"] = val
                return


# ── HTTP / 보안 공통 ──────────────────────────────────────────────────────────

def fetch_html(session, url, referer=None):
    """GET → HTML 텍스트(UTF-8). 응답 크기 상한 적용. 실패 시 None."""
    try:
        h = dict(HEADERS)
        if referer:
            h["Referer"] = referer
        resp = session.get(url, headers=h, timeout=20)
        resp.raise_for_status()
        if resp.content and len(resp.content) > MAX_BYTES:   # 보안: 과대 응답 차단
            log.warning("[땡큐팜] 응답 과대(%d bytes) → 절단", len(resp.content))
            return resp.content[:MAX_BYTES].decode("utf-8", "ignore")
        resp.encoding = "utf-8"
        return resp.text
    except requests.exceptions.Timeout:
        log.error("[땡큐팜] 타임아웃: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("[땡큐팜] 요청 오류: %s - %s", url, e)
    except Exception as e:
        log.error("[땡큐팜] 예상치 못한 오류: %s - %s", url, e)
    return None


def make_soup(html_text):
    """HTML → BeautifulSoup. 위험 태그 제거(보안)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "link", "noscript"]):
        bad.decompose()
    return soup


def list_url(tbl, page):
    """게시판 목록 URL"""
    return "%s/bbs/board.php?tbl=%s&page=%d" % (BASE_URL, tbl, page)


def detail_url(tbl, num):
    """게시판 상세 URL"""
    return "%s/bbs/board.php?tbl=%s&mode=VIEW&num=%s" % (BASE_URL, tbl, num)


# ── 목록 파싱 ──────────────────────────────────────────────────────────────────

def parse_list(html_text, tbl):
    """게시판 목록 HTML → [{num, tbl, title, region, location, gubun_type,
       sale_amount, owner, date, link, status}]"""
    out = []
    if not html_text:
        return out
    soup = make_soup(html_text)
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "mode=VIEW" not in href:
            continue
        m = re.search(r"num=(\d+)", href)
        if not m:
            continue
        num = m.group(1)
        if num in seen:
            continue
        seen.add(num)

        # 링크 내부 텍스트: 1행=제목, 2행=지역+진료과+조제료 (그누보드 <br> 구분)
        parts = [p.strip() for p in a.get_text("\n", strip=True).split("\n") if p.strip()]
        title = clean_text(parts[0], 120) if parts else ""
        sub = clean_text(parts[1], 200) if len(parts) > 1 else ""

        rec = {
            "num": num, "tbl": tbl, "title": title,
            "region": extract_region(sub) if sub else extract_region(title),
            "location": sub, "gubun_type": "", "sale_amount": "",
            "owner": "", "date": "", "status": "판매중",
            "link": detail_url(tbl, num),
        }

        # 진료과/조제료: sub 끝의 숫자 = 조제료(만원)
        if sub:
            amt = re.search(r"(\d{2,5})\s*만?\s*$", sub)
            if amt:
                rec["sale_amount"] = "조제료 %s만원" % amt.group(1)

        # 같은 행(tr)의 보조 셀: 구분(임대/매매/완료), 작성자, 날짜
        row = a.find_parent("tr")
        if row:
            for td in row.find_all("td"):
                ct = clean_text(td.get_text(" ", strip=True), 40)
                if not ct:
                    continue
                g = ct.replace(" ", "")
                if re.fullmatch(r"(임대|매매)(완료)?", g):
                    rec["gubun_type"] = g
                    if "완료" in g:
                        rec["status"] = "거래완료"
                elif re.search(r"20\d{2}[-.]\d{1,2}[-.]\d{1,2}", ct):
                    rec["date"] = normalize_date(ct)
                elif a.get_text(strip=True) not in ct and len(ct) <= 12 and not rec["owner"]:
                    # 제목 링크가 아닌 짧은 셀 = 작성자 후보
                    rec["owner"] = ct
        out.append(rec)
    return out


# ── 상세 파싱 (로그인 상태에서만 본문 노출) ───────────────────────────────────

def parse_detail(html_text, item):
    """상세 HTML(.con01) → item 보강. 핵심: 연락처(전화). 비로그인 시 본문 없음 → 변화 없음."""
    if not html_text:
        return item
    soup = make_soup(html_text)

    # 본문 컨테이너(.con01) 우선, 없으면 .con
    con = soup.select_one(".con01") or soup.select_one(".con") or soup
    text = con.get_text("\n", strip=True) if con else ""
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 1) '라벨 : 값' 매핑(작성자/연락처/날짜 등)
    for line in lines:
        mm = re.match(r"\s*([가-힣A-Za-z/ ]{2,12})\s*[:：]\s*(.+)$", line)
        if mm:
            map_label(item, mm.group(1), mm.group(2))

    # 2) 전화번호 보조 추출(라벨 매핑이 놓친 경우)
    if not item.get("phone"):
        whole = con.get_text(" ", strip=True) if con else ""
        pm = (re.search(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}", whole)
              or re.search(r"0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}", whole))
        if pm:
            item["phone"] = clean_text(pm.group(0), 20)

    # 3) memo = 본문(라벨 메타행 제거 후 설명 위주)
    if lines:
        skip_kw = ("작성자", "연락처", "날짜", "조회", "이전글", "다음글", "목록")
        body_lines = [l for l in lines
                      if not (len(l) < 30 and any(k in l for k in skip_kw))]
        memo = clean_multiline("\n".join(body_lines), 2000)
        if memo:
            item["memo"] = memo

    # 4) 이미지: 본문 내 실제 매물 사진만(로고/배너 제외)
    if not item.get("thumb_url") and con:
        for img in con.find_all("img", src=True):
            u = safe_img_url(img.get("src", ""))
            if u:
                item["thumb_url"] = u
                break

    # 5) 파생값 정리
    if item.get("date"):
        item["date"] = normalize_date(item["date"]) or item["date"]
    return item


# ── 메인 크롤 ──────────────────────────────────────────────────────────────────

def crawl():
    """땡큐팜 매물 수집 → { 'tq_<tbl>_<num>': item_dict }.
    목록은 공개(비로그인) 수집. 로그인 성공 시에만 상세(연락처) 보강.
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    logged_in = login(session)
    if not logged_in:
        log.warning("[땡큐팜] 비로그인 모드: 목록 항목만 수집(연락처 미수집)")

    # 1) 게시판별 목록 순회
    listings = {}   # key=tbl_num
    for tbl, _hint in BOARDS:
        empty_streak = 0
        for page in range(1, MAX_PAGES + 1):
            url = list_url(tbl, page)
            html_text = fetch_html(session, url, referer=BASE_URL + "/")
            rows = parse_list(html_text, tbl)
            new = [r for r in rows if ("%s_%s" % (r["tbl"], r["num"])) not in listings]
            if not new:
                empty_streak += 1
                if empty_streak >= MAX_EMPTY_PAGES:
                    break
            else:
                empty_streak = 0
                for r in new:
                    listings["%s_%s" % (r["tbl"], r["num"])] = r
            time.sleep(REQUEST_DELAY)
            if len(listings) >= MAX_DETAIL:
                break

    log.info("[땡큐팜] 목록 수집 %d건 (로그인=%s)", len(listings), logged_in)

    # 2) 항목 구성 + (로그인 시) 상세 보강
    count = 0
    for key, base in listings.items():
        if count >= MAX_DETAIL:
            break
        item = empty_item()
        item["idx"] = PREFIX + key
        # 목록에서 확보한 값 채우기
        item["title"] = base.get("title", "")
        item["region"] = base.get("region", "")
        item["location"] = base.get("location", "")
        item["gubun_type"] = base.get("gubun_type", "")
        item["sale_amount"] = base.get("sale_amount", "")
        item["owner"] = base.get("owner", "")
        item["date"] = base.get("date", "")
        item["status"] = base.get("status", "판매중")
        item["link"] = base.get("link", detail_url(base["tbl"], base["num"]))

        # 공지/안내글 제외(매물 아님)
        _ttl = item.get("title", "")
        if _ttl.startswith("공지") or "공지사항" in _ttl or "필독" in _ttl:
            log.info("[땡큐팜] 공지글 제외 %s", key)
            continue

        # 로그인 성공 시에만 상세 요청(연락처/본문 보강). 비로그인은 본문이 없어 요청 생략(부하 절감).
        if logged_in:
            try:
                dhtml = fetch_html(session, item["link"], referer=list_url(base["tbl"], 1))
                parse_detail(dhtml, item)
            except Exception as e:
                log.error("[땡큐팜] 상세 파싱 오류 %s: %s", key, e)
            time.sleep(REQUEST_DELAY)

        if not item.get("region") and item.get("title"):
            item["region"] = extract_region(item["title"])

        result[item["idx"]] = item
        count += 1

    log.info("[땡큐팜] 최종 %d건 수집 완료 (로그인=%s)", len(result), logged_in)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = crawl()
    print("수집 건수:", len(data))
    for k, v in list(data.items())[:5]:
        print(k, "|", v.get("title"), "| 지역:", v.get("region"),
              "| 구분:", v.get("gubun_type"), "| 조제료:", v.get("sale_amount"),
              "| 전화:", v.get("phone"))
