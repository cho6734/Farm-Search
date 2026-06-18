# -*- coding: utf-8 -*-
"""
큐팜(qpharm) 매물 크롤러  ─  접두사 qp_

[사이트 구조 - 실제 확인됨 2026-06-19]
  - 플랫폼 : 아임웹(imweb). 매물 = 게시판 글(별도 XHR API 없음, 서버사이드 렌더).
  - 인코딩 : UTF-8
  - 목록   : https://www.qpharm.co.kr/yakguk-listings/?q=BASE64&page=N
             q 파라미터 = PHP 직렬화(keyword_type=all) base64 → 이 없으면 idx 0건 반환됨
  - 로그인 : ✅ 불필요. 목록·상세 모두 공개(비로그인 접근 가능 확인됨 2026-06-19).
             "로그인이 필요합니다" 텍스트는 헤더 내비게이션 버튼이며 본문 접근 차단 아님.
  - 상세   : ?q=BASE64&bmode=view&idx=<글번호>&t=board

[보안 설계] (팜플/약사공론과 동일 원칙)
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱(스크립트 미실행).
  - script/style/iframe/object/embed/link 등 위험 태그 제거.
  - 모든 텍스트는 제어문자 제거 + 길이 제한으로 살균.
  - 모든 URL은 qpharm.co.kr 도메인 + http(s)만 허용(javascript:, data: 차단).
  - 응답 크기 상한 / 페이지 수 상한 / 빈·중복 페이지 감지로 무한루프 방지.

[로그인 정보]
  - GitHub Secrets 불필요. 사이트가 공개이므로 로그인 없이 전체 수집 가능.
  - login() 함수는 호환성 유지용으로 남겨두지만 crawl()에서 호출 안 함.
"""

import os
import re
import time
import json
import logging
from urllib.parse import urlparse, urljoin
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
BASE_URL     = "https://www.qpharm.co.kr"
ALLOWED_HOST = "qpharm.co.kr"                              # 보안: 허용 도메인
LIST_URL     = "https://www.qpharm.co.kr/yakguk-listings/"  # 매물게시판
LOGIN_PROC   = "https://www.qpharm.co.kr/backpg/login.cm"   # 로그인 처리(POST, 현재 불필요)
PREFIX       = "qp_"                                        # 대시보드 출처 접두사
SOURCE       = "qpharm"
# 아임웹 게시판 q 파라미터 = base64(PHP serialize({keyword_type: "all"}))
# 이 파라미터 없이 ?page=N만 넘기면 게시글 idx가 HTML에 포함되지 않음 → 필수
Q_PARAM      = "YToxOntzOjEyOiJrZXl3b3JkX3R5cGUiO3M6MzoiYWxsIjt9"

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
MAX_DETAIL      = 120         # 보안: 상세 수집 최대 건수 상한
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한(5MB)


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
    """상세설명 본문용: 줄바꿈은 살리되 제어문자/과도공백만 정리"""
    if not s:
        return ""
    s = str(s)
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: qpharm 도메인 + http(s)만 허용. 그 외엔 빈 문자열."""
    if not raw:
        return ""
    u = str(raw).strip()
    if u.startswith("//"):
        u = "https:" + u
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


def now_kst():
    """현재 한국시간 문자열"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")


def today_kst():
    """오늘 한국날짜 (YYYY.MM.DD)"""
    return datetime.now(timezone(timedelta(hours=9))).strftime("%Y.%m.%d")


def extract_region(location):
    """위치 문자열에서 시도(첫 단어) 추출"""
    loc = clean_text(location)
    return loc.split()[0] if loc else ""


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
    """로그인 진단 파일 기록(비밀번호 절대 미기록) → raw로 원인 파악용"""
    try:
        _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(_dir, exist_ok=True)
        with open(os.path.join(_dir, "qpharm_login_status.json"), "w", encoding="utf-8") as f:
            json.dump({"ok": bool(ok), "message": str(message)[:200],
                       "id_set": bool(id_set), "ts": now_kst()}, f, ensure_ascii=False)
    except Exception as e:
        log.warning("[큐팜] 진단파일 기록 실패: %s", e)


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
        log.warning("[큐팜] .env 로드 실패(무시): %s", e)


# ── 로그인 ────────────────────────────────────────────────────────────────────

def login(session):
    """아임웹 로그인(평문 폼 POST). 성공 True / 미설정·실패 False(비로그인 → 수집 0건)."""
    load_env()
    uid = (os.environ.get("QPHARM_EMAIL", "").strip()
           or os.environ.get("QPHARM_ID", "").strip())
    passwd = os.environ.get("QPHARM_PASSWORD", "").strip()
    if not uid or not passwd:
        log.info("[큐팜] 계정정보 없음 → 비로그인(큐팜은 비로그인 열람 불가, 0건)")
        write_login_status(False, "no_credentials", False)
        return False
    try:
        # 1) 메인 GET 으로 세션 쿠키 확보
        session.get(BASE_URL + "/", headers=HEADERS, timeout=20)
        # 2) 로그인 폼 POST (실제 폼 필드명 그대로)
        data = {
            "back_url": "", "back_url_auth": "", "used_login_btn": "N",
            "uid": uid, "passwd": passwd, "auto_login": "Y",
        }
        h = dict(HEADERS)
        h.update({
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LIST_URL, "Origin": BASE_URL,
            "X-Requested-With": "XMLHttpRequest",
        })
        resp = session.post(LOGIN_PROC, data=data, headers=h, timeout=20, allow_redirects=True)
        # 아임웹 login.cm 은 보통 JSON({code/msg}) 또는 리다이렉트 반환
        message = ""
        try:
            jj = resp.json()
            message = ("code=%s msg=%s" % (jj.get("code"), jj.get("msg")))[:180]
        except Exception:
            message = "(non-json len=%d)" % len(resp.text or "")
        # 성공 판정: 매물게시판 재요청 후 로그인 차단(openLogin)이 사라졌는지로 확인
        ok = False
        try:
            body = session.get(LIST_URL, headers=HEADERS, timeout=20).text or ""
            # 로그인 상태면 '로그아웃' 노출, 그리고 목록 행에 실제 view 링크(bmode=view)가 생김
            ok = ("로그아웃" in body) or ("bmode=view" in body) or ("logout" in body.lower())
        except Exception:
            pass
        write_login_status(ok, message, True)
        log.info("[큐팜] 로그인 ok=%s msg=%s", ok, message)
        return ok
    except Exception as e:
        log.error("[큐팜] 로그인 오류 → 비로그인 진행: %s", e)
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
# 상세/목록에서 추출한 '라벨 : 값' 들을 스키마 필드로 매핑한다.
# 사이트 라벨이 조금 달라도 키워드 포함으로 대응(추측 셀렉터 의존도 최소화).
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
    ("sale_count",      ["처방", "조제", "처방전", "일평균", "조제건수", "처방건수"]),
    ("sale_amount",     ["매출", "일매출", "월매출", "수익", "순익", "연매출"]),
    ("building_usage",  ["건축물", "용도", "건물용도"]),
    ("approval_date",   ["준공", "사용승인", "준공일"]),
    ("floor_label",     ["층수", "층", "해당층"]),
    ("parking_label",   ["주차"]),
    ("move_in",         ["입주", "입주가능", "입주일"]),
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
                # 면적은 전체 원문도 보존
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
            log.warning("[큐팜] 응답 과대(%d bytes) → 절단", len(resp.content))
            return resp.content[:MAX_BYTES].decode("utf-8", "ignore")
        resp.encoding = "utf-8"
        return resp.text
    except requests.exceptions.Timeout:
        log.error("[큐팜] 타임아웃: %s", url)
    except requests.exceptions.RequestException as e:
        log.error("[큐팜] 요청 오류: %s - %s", url, e)
    except Exception as e:
        log.error("[큐팜] 예상치 못한 오류: %s - %s", url, e)
    return None


def make_soup(html_text):
    """HTML → BeautifulSoup. 위험 태그 제거(보안)."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "link", "noscript"]):
        bad.decompose()
    return soup


def detail_url(idx):
    """아임웹 게시판 상세 URL: q 파라미터 + bmode=view + idx"""
    return "%s?q=%s&bmode=view&idx=%s&t=board" % (LIST_URL, Q_PARAM, idx)


# ── 목록 파싱 ──────────────────────────────────────────────────────────────────

def parse_list(html_text):
    """매물게시판 목록 HTML → [{idx, title, link, (목록상 표시값)}] (로그인 상태에서만 내용 채워짐)"""
    out = []
    if not html_text:
        return out
    soup = make_soup(html_text)
    seen = set()
    # 아임웹 게시판 글 링크는 bmode=view & idx= 를 포함 → 거기서 idx 추출(추측 셀렉터 무의존)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "bmode=view" not in href:
            continue
        m = re.search(r"idx=(\d+)", href)
        if not m:
            continue
        idx = m.group(1)
        if idx in seen:
            continue
        seen.add(idx)
        title = clean_text(a.get_text(" ", strip=True), 120)
        # 같은 행(li/tr)에서 보조 정보 텍스트 확보
        row = a.find_parent(["li", "tr"])
        row_txt = clean_text(row.get_text(" ", strip=True), 250) if row else title
        out.append({"idx": idx, "title": title, "link": detail_url(idx),
                    "row_text": row_txt})
    return out


# ── 상세 파싱 (한글 라벨 기준 매핑) ───────────────────────────────────────────

def _largest_text_block(soup):
    """게시판 본문(상세설명) 후보: 알려진 컨테이너 → 없으면 가장 긴 텍스트 블록."""
    candidates = [
        ".board_txt_area",      # 큐팜 아임웹: 실제 확인된 셀렉터 (2026-06-19)
        ".board_view_content", ".view_content", ".bd_view", ".se-viewer",
        ".editor_view", ".board_text", ".content_view", "#contents .content",
        ".post_content", ".se-main-container",
    ]
    for sel in candidates:
        el = soup.select_one(sel)
        if el:
            t = el.get_text("\n", strip=True)
            if len(t) > 30:
                return t
    # 폴백: div 중 가장 긴 텍스트
    best = ""
    for div in soup.find_all(["div", "section", "article"]):
        t = div.get_text("\n", strip=True)
        if len(t) > len(best):
            best = t
    return best


def parse_detail(html_text, item):
    """상세 HTML → item 채움. 테이블/정의리스트/'라벨:값' + 본문 + 전화번호 보조추출."""
    if not html_text:
        return item
    soup = make_soup(html_text)

    # 1) 표(th/td, td/td) 라벨-값 매핑
    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"])
        if len(cells) >= 2:
            label = cells[0].get_text(" ", strip=True)
            value = cells[1].get_text(" ", strip=True)
            map_label(item, label, value)

    # 2) 정의리스트(dt/dd) 매핑
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt"); dds = dl.find_all("dd")
        for dt, dd in zip(dts, dds):
            map_label(item, dt.get_text(" ", strip=True), dd.get_text(" ", strip=True))

    # 3) 본문(상세설명) 추출
    body = _largest_text_block(soup)
    body = clean_multiline(body, 2000)
    lines = [l.strip() for l in body.split("\n") if l.strip()] if body else []

    # 3-1) 큐팜 게시판 본문 구조 파싱
    #      [0]제목(지역ㅣ거래유형ㅣ특징) [1]글쓴이 [2]카테고리 [3]날짜 [4]조회수 [5+]본문
    if lines:
        if not item.get("title"):
            item["title"] = clean_text(lines[0], 120)
        # 제목을 'ㅣ/|/│' 로 분리 → 지역·거래유형 추출
        seg = [p.strip() for p in re.split(r"[ㅣ|│/]+", lines[0]) if p.strip()]
        if seg:
            if not item.get("region"):
                item["region"] = clean_text(seg[0], 10)
            if len(seg) > 1 and not item.get("gubun_type"):
                item["gubun_type"] = clean_text(seg[1], 20)
        # 날짜 줄(YYYY-MM-DD / YYYY.MM.DD)
        for l in lines[:8]:
            if re.match(r"^20\d{2}[-.]\d{1,2}[-.]\d{1,2}$", l):
                item["date"] = normalize_date(l)
                break

    # 4) 본문 '라벨 : 값' 패턴 매핑(텍스트형 게시글 대응)
    for line in lines:
        mm = re.match(r"\s*([가-힣A-Za-z/ ]{2,12})\s*[:：]\s*(.+)$", line)
        if mm:
            map_label(item, mm.group(1), mm.group(2))

    # 4-1) 면적 정규식 보조(본문에 ㎡/평 표기 시)
    if not item.get("area_label") and body:
        am = re.search(r"\d+(?:\.\d+)?\s*㎡(?:\s*\(?\s*\d+(?:\.\d+)?\s*평\)?)?|\d+(?:\.\d+)?\s*평", body)
        if am:
            item["area_label"] = clean_text(am.group(0), 40)
            item["area_full"] = item["area_label"]

    # 4-2) memo 정제: 헤더(제목줄/글쓴이/카테고리/조회수/날짜) 제거, 실제 본문만
    if lines:
        skip = ("관리자", "매물게시판", "조회수", "공지사항")
        clean_lines = []
        for i, l in enumerate(lines):
            if i == 0:                                          # 제목 줄 → title로 분리
                continue
            if re.match(r"^20\d{2}[-.]\d{1,2}[-.]\d{1,2}$", l):  # 날짜 줄 제거
                continue
            if len(l) < 20 and any(k in l for k in skip):       # 헤더 잡음 제거
                continue
            clean_lines.append(l)
        item["memo"] = "\n".join(clean_lines)[:2000]

    # 5) 전화번호 보조 추출(라벨 매핑 실패 시)
    if not item.get("phone"):
        whole = soup.get_text(" ", strip=True)
        pm = re.search(r"01[016789][-\s.]?\d{3,4}[-\s.]?\d{4}", whole)
        if not pm:
            pm = re.search(r"0\d{1,2}[-\s.]?\d{3,4}[-\s.]?\d{4}", whole)
        if pm:
            item["phone"] = clean_text(pm.group(0), 20)

    # 6) 파생값 정리
    if not item.get("memo") and body:   # 정제된 memo가 없을 때만 원문 사용
        item["memo"] = body
    if item.get("location") and not item.get("region"):
        item["region"] = extract_region(item["location"])
    if item.get("date"):
        item["date"] = normalize_date(item["date"]) or item["date"]
    if item.get("approval_date"):
        item["approval_date"] = normalize_date(item["approval_date"]) or item["approval_date"]

    # 7) 썸네일(있으면)
    if not item.get("thumb_url"):
        img = soup.find("img", src=True)
        if img:
            item["thumb_url"] = safe_url(img["src"])

    return item


# ── 메인 크롤 ──────────────────────────────────────────────────────────────────

def crawl():
    """큐팜 매물 수집 → { 'qp_<idx>': item_dict }.
    큐팜은 로그인 불필요(공개 사이트). q 파라미터 없으면 idx 미포함 → 필수.
    """
    result = {}
    session = requests.Session()
    session.headers.update(HEADERS)

    # 사이트가 공개이므로 로그인 불필요 → 바로 수집 시작
    # (login() 함수는 Cloudflare 세션쿠키 확보용으로 메인 페이지만 GET)
    try:
        session.get(BASE_URL + "/", headers=HEADERS, timeout=20)
    except Exception as e:
        log.warning("[큐팜] 메인 페이지 GET 실패(계속 진행): %s", e)

    # 1) 목록 페이지 순회(아임웹 ?q=BASE64&page=N), 빈/중복 감지로 종료
    listings = {}
    empty_streak = 0
    for page in range(1, MAX_PAGES + 1):
        url = "%s?q=%s&page=%d" % (LIST_URL, Q_PARAM, page)
        html_text = fetch_html(session, url, referer=LIST_URL)
        rows = parse_list(html_text)
        new = [r for r in rows if r["idx"] not in listings]
        if not new:
            empty_streak += 1
            if empty_streak >= MAX_EMPTY_PAGES:
                break
        else:
            empty_streak = 0
            for r in new:
                listings[r["idx"]] = r
        time.sleep(REQUEST_DELAY)
        if len(listings) >= MAX_DETAIL:
            break

    log.info("[큐팜] 목록 수집 %d건", len(listings))

    # 2) 상세 enrich
    count = 0
    for idx, base in listings.items():
        if count >= MAX_DETAIL:
            break
        item = empty_item()
        item["idx"] = PREFIX + idx
        item["title"] = base.get("title", "")
        item["link"] = base.get("link", detail_url(idx))
        try:
            dhtml = fetch_html(session, base["link"], referer=LIST_URL)
            parse_detail(dhtml, item)
        except Exception as e:
            log.error("[큐팜] 상세 파싱 오류 idx=%s: %s", idx, e)
        # 제목/지역 보조
        if not item.get("region") and item.get("title"):
            item["region"] = extract_region(item["title"])
        # 공지글 제외(매물 아님): 제목이 '공지'로 시작하거나 '*공지*' 포함
        _ttl = item.get("title", "")
        if _ttl.startswith("공지") or "*공지*" in (item.get("memo", "")[:30]) or "공지사항" in _ttl:
            log.info("[큐팜] 공지글 제외 idx=%s", idx)
            count += 1
            time.sleep(REQUEST_DELAY)
            continue
        result[PREFIX + idx] = item
        count += 1
        time.sleep(REQUEST_DELAY)

    log.info("[큐팜] 최종 %d건 수집 완료", len(result))
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    data = crawl()
    print("수집 건수:", len(data))
    for k, v in list(data.items())[:3]:
        print(k, "|", v.get("title"), "| 전화:", v.get("phone"),
              "| 면적:", v.get("area_label"), "| memo:", (v.get("memo") or "")[:60])

