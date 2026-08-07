# -*- coding: utf-8 -*-
"""
팜플(pharmple) 매물 크롤러
API:
  - 일반 목록   : POST https://pharmple.co.kr/sale/getmaemullist
  - 프리미엄 목록: POST https://pharmple.co.kr/sale/getPremiumMaemullist
응답: HTML fragment (div.item) - 로그인 불필요

[보안 설계]
  - 응답은 텍스트(HTML)로만 받아 BeautifulSoup(html.parser)로 파싱한다.
    스크립트를 실행하지 않으므로 악성 JS 실행 위험이 없다.
  - 파싱 전 script/style/iframe/object/embed/link 태그를 제거한다.
  - 모든 텍스트는 제어문자 제거 + 길이 제한으로 살균한다.
  - 모든 링크/이미지 URL은 pharmple.co.kr 도메인 + http(s)만 허용한다.
    (javascript:, data: 등 위험 스킴 및 외부 도메인 차단)
  - 응답 크기 상한, 페이지 수 상한, 중복 페이지 감지로 무한루프/과대응답을 방지한다.
"""

import os
import re
import time
import base64
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
LIST_API     = "https://pharmple.co.kr/sale/getmaemullist"
PREMIUM_API  = "https://pharmple.co.kr/sale/getPremiumMaemullist"
BASE_URL     = "https://pharmple.co.kr"
ALLOWED_HOST = "pharmple.co.kr"          # 보안: 허용 도메인
DETAIL_URL   = "https://pharmple.co.kr/sale/view"          # 상세(로그인 필요, 일반 GET·XHR헤더 없이)
LOGIN_PAGE   = "https://pharmple.co.kr/membership/login"
LOGIN_PROC   = "https://pharmple.co.kr/membership/login_proc/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://pharmple.co.kr/sale/list",
    "Accept": "text/html, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}

BASE_PARAMS = {
    "page": "1", "location": "", "area": "", "price": "",
    "type": "", "keyword": "", "level": "", "hyungtae": "",
}

REQUEST_DELAY   = 1.0    # 페이지당 딜레이(초) - 서버 부하 방지
MAX_EMPTY_PAGES = 2      # 빈/중복 페이지 연속 N회면 종료
MAX_PAGES       = 50     # 보안: 페이지 수 상한 (무한루프 방지)
MAX_BYTES       = 5_000_000   # 보안: 응답 크기 상한 (5MB)


# ── 유틸리티 / 보안 살균 ──────────────────────────────────────────────────────

def clean_text(s, max_len=300):
    """텍스트 살균: 제어문자 제거 + 공백 정리 + 길이 제한"""
    if not s:
        return ""
    s = str(s)
    # 보안: 제어문자(널바이트 등) 제거
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:max_len]


def safe_url(raw):
    """URL 살균: pharmple 도메인 + http(s)만 허용. 그 외엔 빈 문자열."""
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
    # 보안: 허용 도메인만
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
    """'26.01.01' / '2026-01-01' → '2026.01.01'. 날짜 형식 아니면 ''"""
    if not raw:
        return ""
    m = re.search(r"(\d{2,4})[.\-/](\d{1,2})[.\-/](\d{1,2})", raw)
    if not m:
        return ""
    y, mo, d = m.group(1), m.group(2), m.group(3)
    if len(y) == 2:                 # 2자리 연도 → 2000년대로 보정
        y = "20" + y
    return f"{y}.{mo.zfill(2)}.{d.zfill(2)}"


def decode_idx(enc):
    """base64로 인코딩된 idx → 평문 숫자 id"""
    try:
        return base64.b64decode(enc).decode("utf-8", "ignore")
    except Exception:
        return ""


def infer_gubun_type(text):
    """텍스트에서 매물 유형(매매/임대/신규분양) 추론"""
    t = text or ""
    if "신규" in t or "분양" in t:
        return "신규분양"
    if "매매" in t:
        return "매매"
    if "임대" in t:
        return "임대"
    return ""


# ── 파싱 ──────────────────────────────────────────────────────────────────────

def _dl_map(card):
    """카드 내 <dl><dt>키</dt><dd>값</dd></dl> → {키: 값} dict"""
    out = {}
    for dl in card.select("dl"):
        dt = dl.find("dt")
        dd = dl.find("dd")
        if dt and dd:
            out[clean_text(dt.get_text(), 30)] = clean_text(dd.get_text(), 120)
    return out


def _extract_id(card):
    """매물 id 추출: 1순위 data-scrap-idx(평문), 2순위 onclick의 base64 idx"""
    fav = card.select_one("[data-scrap-idx]")
    if fav and clean_text(fav.get("data-scrap-idx", ""), 20):
        return clean_text(fav.get("data-scrap-idx", ""), 20)
    oc = card.get("onclick", "") or ""
    m = re.search(r"idx=([A-Za-z0-9+/=]+)", oc)
    if m:
        dec = decode_idx(m.group(1))
        return dec if dec.isdigit() else clean_text(m.group(1), 30)
    return ""


def _extract_link(card):
    """onclick에서 상세 링크 추출 후 살균"""
    oc = card.get("onclick", "") or ""
    m = re.search(r"href='([^']+)'", oc)
    return safe_url(m.group(1)) if m else ""


def _info_map(card):
    """일반매물 .item-info 안 <li><b>라벨</b> 값</li> → {라벨: 값}"""
    out = {}
    for li in card.select(".item-info li"):
        b = li.find("b")
        if b:
            label = clean_text(b.get_text(), 20)
            val = clean_text(li.get_text().replace(b.get_text(), "", 1), 60)
            out[label] = val
    return out


def parse_item_card(card, is_premium=False):
    """div.item 카드 → 표준 스키마 dict. 실패 시 None."""
    try:
        item_id = _extract_id(card)
        if not item_id:
            log.debug("ID 없음 → 스킵")
            return None

        key  = f"pp_{item_id}"
        href = _extract_link(card)
        dm   = _dl_map(card)

        if is_premium:
            # 프리미엄: 일부 정보가 회원 전용으로 잠겨 있음(지역/상권 placeholder)
            location   = clean_text((card.select_one("p.area") or _empty()).get_text(), 100)
            sale_count = dm.get("조제", "")
            daily      = dm.get("일매", "")
            price      = ""                      # 잠김
            trade_area = ""                      # placeholder 무시
            date_str   = normalize_date(dm.get("등록일", ""))
            gubun_type = ""                      # 잠김
            title      = f"[프리미엄] {location} 약국 매물".strip()
            is_new     = False
        else:
            location   = dm.get("지역", "")
            trade_area = dm.get("상권", "")
            kind_el    = card.select_one(".item-kindBox")
            gubun_type = clean_text(kind_el.get_text(), 10) if kind_el else ""
            tit_el     = card.select_one(".item-tit")
            title      = ""
            if tit_el:
                t = tit_el.get_text()
                if gubun_type:
                    t = t.replace(gubun_type, "", 1)
                title = clean_text(t, 200)
            info       = _info_map(card)
            sale_count = info.get("월조제", "") or info.get("조제", "")
            daily      = info.get("일매", "")
            price      = (info.get("임대료", "") or info.get("매매가", "")
                          or info.get("분양가", "") or info.get("매가", ""))
            raw_reg    = dm.get("등록일", "")
            date_str   = normalize_date(raw_reg)
            is_new     = ("NEW" in raw_reg.upper())
            if not date_str and is_new:
                date_str = today_kst()
            if not gubun_type:
                gubun_type = infer_gubun_type(title)

        region = extract_region(location)

        # ── 썸네일 (있으면) : URL 살균 후 저장 ──
        thumb_url = ""
        img = card.find("img")
        if img:
            src = img.get("src") or img.get("data-src") or img.get("data-lazy") or ""
            src = safe_url(src)
            if src and "no_image" not in src and "placeholder" not in src:
                thumb_url = src

        # ── 거래유형(중개매물/약사직거래) 추론 ──
        full_text = card.get_text()
        if is_premium:
            seller_type = "중개매물"   # 프리미엄은 중개사/기업회원 전용 매물
        elif "중개" in full_text:
            seller_type = "중개매물"
        elif "직거래" in full_text:
            seller_type = "약사직거래"
        else:
            seller_type = ""

        # ── 태그 ──
        tags = []
        if seller_type:
            tags.append(seller_type)
        if is_premium:
            tags.append("프리미엄")
        if (not is_premium) and is_new:
            tags.append("NEW")
        if gubun_type:
            tags.append(gubun_type)

        # ── 메모 ──
        memo = []
        if location:   memo.append(f"지역: {location}")
        if trade_area: memo.append(f"상권: {trade_area}")
        if sale_count: memo.append(f"월조제: {sale_count}")
        if daily:      memo.append(f"일매: {daily}")
        if price:      memo.append(f"가격: {price}")
        if href:       memo.append(f"링크: {href}")

        return {
            "idx":          key,
            "pharmple_id":  item_id,
            "source":       "pharmple",
            "title":        title or f"팜플 매물 #{item_id}",
            "region":       region,
            "location":     location,
            "price":        price,
            "rent":         "",
            "phone":        "",          # 목록에서 비공개
            "owner":        "",
            "date":         date_str,
            "area_label":   "",
            "area_full":    "",
            "trade_area":   trade_area,
            "gubun_type":   gubun_type,
            "seller_type":  seller_type,   # 중개매물 / 약사직거래
            "sale_count":   sale_count,  # 월 조제(처방) 수입
            "sale_amount":  "",
            "daily_sales":  daily,       # 일매(일 매출)
            "thumb_url":    thumb_url,
            "tags":         ", ".join(dict.fromkeys(tags)),
            "memo":         "\n".join(memo),
            "special_flag": "",
            "status":       "active",
            "collected_at": now_kst(),
            "link":         href,
            "is_premium":   is_premium,
        }
    except Exception as e:
        log.warning("parse_item_card 오류: %s", e, exc_info=True)
        return None


def _empty():
    """select_one 결과가 None일 때 .get_text() 호출용 더미"""
    class _D:
        def get_text(self, *a, **k):
            return ""
    return _D()


def parse_html_response(html_text, is_premium=False):
    """API 응답 HTML → 매물 카드 dict 리스트 (보안 살균 포함)"""
    items = []
    if not html_text or not html_text.strip():
        return items
    soup = BeautifulSoup(html_text, "html.parser")
    # 보안: 실행 가능/위험 요소 제거
    for bad in soup(["script", "style", "iframe", "object", "embed", "link"]):
        bad.decompose()
    for card in soup.select("div.item"):
        parsed = parse_item_card(card, is_premium=is_premium)
        if parsed:
            items.append(parsed)
    return items


# ── API 호출 ──────────────────────────────────────────────────────────────────

def fetch_page(session, api_url, page):
    """POST 요청 → HTML 텍스트. 실패 시 None. (응답 크기 상한 적용)"""
    body = {**BASE_PARAMS, "page": str(page)}
    try:
        resp = session.post(api_url, data=body, headers=HEADERS, timeout=20)
        resp.raise_for_status()
        # 보안: 과대 응답 차단
        if resp.content and len(resp.content) > MAX_BYTES:
            log.warning("응답이 너무 큼(%d bytes) → 절단", len(resp.content))
            return resp.content[:MAX_BYTES].decode("utf-8", "ignore")
        resp.encoding = "utf-8"
        return resp.text
    except requests.exceptions.Timeout:
        log.error("타임아웃: %s p%d", api_url, page)
    except requests.exceptions.RequestException as e:
        log.error("요청 오류: %s p%d - %s", api_url, page, e)
    except Exception as e:
        log.error("예상치 못한 오류: %s p%d - %s", api_url, page, e)
    return None


def load_env():
    """# .env 로드(로컬용). 실패해도 무시. GitHub Actions는 env 주입."""
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
        log.warning("[팜플] .env 로드 실패(무시): %s", e)


def login(session):
    """# 팜플 로그인(평문 폼 POST). 성공 True / 미설정·실패 False(비로그인=상세 불가)."""
    load_env()
    userid = (os.environ.get("PHARMPLE_EMAIL", "").strip()
              or os.environ.get("PHARMPLE_ID", "").strip())
    passwd = os.environ.get("PHARMPLE_PASSWORD", "").strip()
    if not userid or not passwd:
        log.info("[팜플] 계정정보 없음 → 비로그인(상세 수집 불가, 목록만)")
        return False
    try:
        session.get(LOGIN_PAGE, headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20)
        data = {
            "mode": "LOGIN", "login_type": "1", "user_agent": "Chrome",
            "login_go": "", "userid": userid, "passwd": passwd,
            # 서버가 어떤 필드명을 읽는지 불확실 → 화면 입력 필드명도 함께 전송(둘 다 커버)
            "loginId1": userid, "loginPwd1": passwd,
            "loginId": userid, "loginPwd": passwd,
            "isSaveId": "", "isSavePwd": "",
        }
        h = {"User-Agent": HEADERS["User-Agent"],
             "Content-Type": "application/x-www-form-urlencoded",
             "Referer": LOGIN_PAGE, "Origin": BASE_URL}
        resp = session.post(LOGIN_PROC, data=data, headers=h, timeout=20, allow_redirects=True)
        # login_proc 는 JSON {status, message} 반환 → status 로 직접 판정
        status, message = "", ""
        try:
            jj = resp.json()
            status = str(jj.get("status", "")); message = str(jj.get("message", ""))[:120]
        except Exception:
            message = "(non-json len=%d)" % len(resp.text or "")
        # 진단 파일 기록 (비밀번호는 절대 기록하지 않음) → raw로 읽어 원인 파악
        try:
            import json as _json
            _dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
            os.makedirs(_dir, exist_ok=True)
            with open(os.path.join(_dir, "pharmple_login_status.json"), "w", encoding="utf-8") as _f:
                _json.dump({"status": status, "message": message,
                            "id_set": bool(userid), "ts": now_kst()}, _f, ensure_ascii=False)
        except Exception as _e:
            log.warning("[팜플] 진단파일 기록 실패: %s", _e)
        ok = (status.lower() == "success")
        if not ok:
            # status 미확인 시 쿠키 기반 보조 판정(메인 로그아웃 노출)
            try:
                body = session.get(BASE_URL + "/", headers={"User-Agent": HEADERS["User-Agent"]}, timeout=20).text or ""
                ok = "로그아웃" in body
            except Exception:
                pass
        log.info("[팜플] 로그인 status=%s ok=%s msg=%s", status, ok, message)
        if ok:
            log.info("[팜플] 로그인 성공 → 상세 수집 가능")
            return True
        log.warning("[팜플] 로그인 실패 → 비로그인 진행 (msg=%s)", message)
        return False
    except Exception as e:
        log.error("[팜플] 로그인 오류 → 비로그인 진행: %s", e)
        return False


def fetch_detail(session, pharmple_id):
    """# 팜플 상세 GET /sale/view?idx=base64(id). XHR헤더 없이 호출해야 전체 HTML이 옴."""
    if not pharmple_id:
        return None
    try:
        idx_b64 = base64.b64encode(str(pharmple_id).encode()).decode()
        h = {"User-Agent": HEADERS["User-Agent"],
             "Referer": "https://pharmple.co.kr/sale/list",
             "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
        resp = session.get(DETAIL_URL, params={"idx": idx_b64}, headers=h, timeout=20)
        resp.raise_for_status()
        raw = resp.content or b""
        if len(raw) > MAX_BYTES:
            raw = raw[:MAX_BYTES]
        resp.encoding = "utf-8"
        html = raw.decode("utf-8", "ignore")
        # 비로그인/접근불가/리다이렉트 stub(101바이트대)면 None
        if len(html) < 2000 or "입점 중인 업체가 아니" in html or "찾을 수 없" in html:
            return None
        return html
    except Exception as e:
        log.warning("[팜플] 상세 %s 조회 실패: %s", pharmple_id, e)
        return None


def parse_detail(html):
    """# 상세 HTML → {라벨:값} dict + 상세설명 본문. 비면 {}."""
    out = {}
    if not html:
        return out
    soup = BeautifulSoup(html, "html.parser")
    for bad in soup(["script", "style", "iframe", "object", "embed", "link"]):
        bad.decompose()
    # dl dt/dd
    for dl in soup.select("dl"):
        dt = dl.find("dt"); dd = dl.find("dd")
        if dt and dd:
            label = clean_text(dt.get_text(), 20)
            if label:
                out[label] = clean_text(dd.get_text(), 200)
    # 상세설명: <h5>상세설명</h5> 다음 .txt
    for h in soup.select("h5, h4, h3, strong, .tit"):
        if clean_text(h.get_text(), 12) == "상세설명":
            nxt = h.find_next(class_="txt") or h.find_next_sibling()
            if nxt:
                out["상세설명"] = clean_text(nxt.get_text(separator="\n"), 2000)
            break
    return out


def apply_detail(item, det):
    """# 상세 dict를 스키마로 매핑(전화 + 메모 전문). 성공 True."""
    if not det:
        return False
    g = lambda k: det.get(k, "")
    if g("대표전화"):
        item["phone"] = g("대표전화")
    if g("상권"):
        item["trade_area"] = g("상권")
    if g("면적"):
        item["area_full"] = g("면적"); item["area_label"] = g("면적")
    if g("관리비"):
        item["maintenance_fee"] = g("관리비")
    if g("형태"):
        item["form_type"] = g("형태")
    # 수익구조: 조제료(월) / 일반매출(일)
    profit = g("수익구조")
    if profit:
        m1 = re.search(r"조제료[^\d]*([\d,]+\s*만원)", profit)
        m2 = re.search(r"일반매출[^\d]*([\d,]+\s*만원)", profit)
        if m1: item["sale_count"]  = m1.group(1).replace(" ", "") + "/월"
        if m2: item["sale_amount"] = m2.group(1).replace(" ", "") + "/일"
    if g("거래유형"):
        item["price"] = g("거래유형")
    owner = " / ".join([x for x in [g("대표자"), g("등록번호")] if x])
    if owner:
        item["owner"] = owner
    if g("광고게시일"):
        item["date"] = g("광고게시일")
    # 메모(전 항목 + 상세설명 본문)
    memo = []
    for k in ["거래유형","상권","면적","관리비","형태","수익구조","주소","대표자","대표전화","연락 가능시간","광고게시일"]:
        if g(k):
            memo.append(f"{k}: {g(k)}")
    if g("상세설명"):
        memo.append(f"상세: {g('상세설명')}")
    if memo:
        item["memo"] = "\n".join(memo)
    return True


def crawl_api(session, api_url, is_premium=False):
    """단일 엔드포인트 페이지네이션 수집 → {key: item}"""
    label = "프리미엄" if is_premium else "일반"
    items = {}
    page = 1
    empty = 0
    log.info("[팜플 %s] 수집 시작 - %s", label, api_url)
    while page <= MAX_PAGES:
        html = fetch_page(session, api_url, page)
        if html is None:
            log.warning("[팜플 %s] p%d 응답 없음 → 중단", label, page)
            break
        page_items = parse_html_response(html, is_premium=is_premium)
        new = 0
        for it in page_items:
            if it["idx"] not in items:
                items[it["idx"]] = it
                new += 1
        # 빈 페이지 또는 새 항목 0(중복 페이지) → 종료 카운트
        if not page_items or new == 0:
            empty += 1
            log.info("[팜플 %s] p%d 신규 0 (%d/%d)", label, page, empty, MAX_EMPTY_PAGES)
            if empty >= MAX_EMPTY_PAGES:
                log.info("[팜플 %s] 종료 (누적 %d건)", label, len(items))
                break
        else:
            empty = 0
            log.info("[팜플 %s] p%d 신규 %d건 (누적 %d건)", label, page, new, len(items))
        page += 1
        time.sleep(REQUEST_DELAY)
    return items


def crawl():
    """팜플 일반 + 프리미엄 전체 수집 → {pp_id: item} dict"""
    session = requests.Session()
    session.headers.update({"User-Agent": HEADERS["User-Agent"],
                            "Referer": HEADERS["Referer"]})
    logged_in = login(session)  # 상세 수집에 필요(계정 없으면 목록만)
    log.info("[팜플] 로그인 상태: %s", "로그인" if logged_in else "비로그인")
    all_items = {}

    # 1) 일반 매물
    try:
        normal = crawl_api(session, LIST_API, is_premium=False)
        all_items.update(normal)
        log.info("[팜플] 일반 %d건", len(normal))
    except Exception as e:
        log.error("[팜플] 일반 수집 실패: %s", e, exc_info=True)

    time.sleep(REQUEST_DELAY)

    # 2) 프리미엄 매물 (있으면 일반 위에 덮어쓰기)
    try:
        premium = crawl_api(session, PREMIUM_API, is_premium=True)
        ov = len(set(all_items) & set(premium))
        if ov:
            log.info("[팜플] 일반-프리미엄 중복 %d건 → 프리미엄 우선", ov)
        all_items.update(premium)
        log.info("[팜플] 프리미엄 %d건", len(premium))
    except Exception as e:
        log.error("[팜플] 프리미엄 수집 실패: %s", e, exc_info=True)

    # ── 상세 보강 ── (로그인 쿠키로 상세 GET. 처음 몇 건이 모두 실패하면 접근불가로 보고 중단)
    enriched = 0
    consec_fail = 0
    checked = 0
    for it in all_items.values():
        checked += 1
        det = parse_detail(fetch_detail(session, it.get("pharmple_id")))
        if apply_detail(it, det):
            enriched += 1
            consec_fail = 0
        else:
            consec_fail += 1
        # 초반 5건 연속 실패 = 로그인/권한 문제 → 더 진행 안 하고 중단(시간 절약)
        if checked >= 5 and enriched == 0 and consec_fail >= 5:
            log.warning("[팜플] 상세 5건 연속 실패 → 접근불가로 판단, 상세보강 중단(목록 데이터 유지)")
            break
        time.sleep(REQUEST_DELAY)
    log.info("[팜플] 상세 보강: %d/%d건 (전화·면적·수익구조·상세설명 등)", enriched, len(all_items))

    log.info("팜플 크롤링 완료: 총 %d건", len(all_items))
    return all_items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = crawl()
    print(f"\n총 {len(result)}건")
    for k, v in list(result.items())[:3]:
        print(f"[{k}] {v['title']} | {v['location']} | {v['price']} | 조제:{v['sale_count']}")
