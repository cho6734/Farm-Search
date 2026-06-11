# -*- coding: utf-8 -*-
"""
팜올플러스(pharmallplus) 매물 크롤러
API: https://open-api.pharmallplus.com/v1/listings
인증: 이메일/비밀번호 로그인 → JWT access_token (RSA OAEP SHA-256)
"""
import json, re, time, base64, logging, pathlib, os
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

log = logging.getLogger(__name__)

ROOT       = pathlib.Path(__file__).resolve().parent
LIST_API   = "https://open-api.pharmallplus.com/v1/listings"
DETAIL_API = "https://open-api.pharmallplus.com/v1/listings/{id}"
LOGIN_API  = "https://api.pharmallplus.com/v1/users/signin"

# 이미지 허용 도메인 (화이트리스트)
ALLOWED_IMG_DOMAINS = ["i.pharmallplus.com"]

# 코드 → 한국어 매핑
TRADE_TYPE_MAP = {"SALE": "매매", "RENT": "임대", "PRE_SALE": "분양"}
OP_TYPE_MAP    = {"OPERATING": "기존약국", "NEW_PHARMACY": "신규약국"}
AREA_TYPE_MAP  = {
    "LOCAL": "로컬", "LOCAL_CLINIC": "로컬의원", "LOCAL_HOSPITAL": "로컬병원",
    "GENERAL": "일반상권", "STATION": "역세권", "APARTMENT": "아파트단지",
    "MEDICAL_CENTER": "메디컬센터", "HOSPITAL_NEARBY": "병원인근",
}
# 형태 (판매유형)
SALES_TYPE_MAP = {
    "PRESCRIPTION_OTC": "조제 + 매약",
    "PRESCRIPTION":     "조제",
    "OTC":              "매약",
}
# 건물용도 (property_type)
PROPERTY_TYPE_MAP = {
    "TYPE_1_NEIGHBORHOOD_FACILITY":  "제 1종 근린시설",
    "TYPE_2_NEIGHBORHOOD_FACILITY":  "제 2종 근린시설",
    "APARTMENT":                     "아파트",
    "OFFICE_BUILDING":               "업무용빌딩",
    "MEDICAL_FACILITY":              "의료시설",
}
# 화장실 유형
BATHROOM_TYPE_MAP = {
    "SHARED":  "공용",
    "PRIVATE": "전용",
    "BOTH":    "공용+전용",
}


# ── 보안: 4단계 살균 함수 ──────────────────────────────────────────────────

def sanitize_text(v, max_len=500):
    """# 1~4단계: HTML태그 제거 → javascript: 제거 → 스크립트키워드 제거 → 길이제한"""
    if v is None:
        return ""
    s = str(v)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"javascript\s*:", "", s, flags=re.I)
    s = re.sub(r"(on\w+\s*=|<script|</script)", "", s, flags=re.I)
    return s.strip()[:max_len]

def sanitize_number(v):
    """# 숫자 타입 검증 - 숫자가 아니면 None 반환"""
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None

def sanitize_url(v):
    """# URL 화이트리스트 검증 - 허용 도메인만 통과"""
    if not v:
        return ""
    v = str(v).strip()
    if v.startswith("https://") and any(d in v for d in ALLOWED_IMG_DOMAINS):
        return v
    return ""


# ── 인증 ──────────────────────────────────────────────────────────────────

def load_env():
    """# .env 파일에서 환경변수 로드"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

def fix_pem(raw_pem: str) -> str:
    """# 공백으로 구분된 PEM 문자열 → 올바른 PEM 형식으로 변환"""
    inner = (raw_pem
             .replace("-----BEGIN PUBLIC KEY-----", "")
             .replace("-----END PUBLIC KEY-----", "")
             .replace(" ", ""))
    lines = [inner[i:i+64] for i in range(0, len(inner), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----"


def login():
    """# 팜올 로그인 → access_token 반환 (RSA OAEP SHA-256 암호화)"""
    load_env()
    email    = os.environ.get("PHARMALL_EMAIL", "").strip()
    password = os.environ.get("PHARMALL_PASSWORD", "").strip()
    if not email or not password:
        raise ValueError("PHARMALL_EMAIL, PHARMALL_PASSWORD가 .env에 없습니다")

    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

        # Session으로 쿠키 공유
        sess = requests.Session()

        # 1단계: RSA 공개키 조회
        pk_r = sess.get("https://api.pharmallplus.com/v1/users/public-key", timeout=10)
        pk_r.raise_for_status()
        raw_pem = pk_r.json()["data"]["public_key"]
        log.info(f"팜올 공개키 조회 성공 / 쿠키: {dict(sess.cookies)}")

        # 2단계: RSA OAEP 암호화 (SHA-1 시도 → 실패 시 SHA-256 재시도)
        pub_key = serialization.load_pem_public_key(fix_pem(raw_pem).encode())

        def try_login(padding_algo):
            """# 지정된 패딩으로 암호화 후 로그인 시도"""
            enc = pub_key.encrypt(
                password.encode("utf-8"),
                asym_padding.OAEP(
                    mgf=asym_padding.MGF1(algorithm=padding_algo()),
                    algorithm=padding_algo(),
                    label=None
                )
            )
            pw_enc = base64.b64encode(enc).decode("utf-8")
            resp = sess.post(LOGIN_API, json={"email": email, "password": pw_enc}, timeout=10)
            log.info(f"로그인 응답 [{padding_algo.__name__}]: {resp.status_code} / {resp.text[:300]}")
            if resp.status_code == 200:
                j = resp.json()
                if j.get("meta", {}).get("success"):
                    return j["data"]["access_token"]
            return None

        # SHA-1 먼저 시도 (원래 방식)
        token = try_login(hashes.SHA1)
        if not token:
            log.warning("SHA-1 로그인 실패 → SHA-256 재시도")
            token = try_login(hashes.SHA256)
        if not token:
            raise ValueError("SHA-1, SHA-256 모두 로그인 실패")
        log.info("팜올 로그인 성공")
        return token
    except Exception as e:
        log.error(f"팜올 로그인 에러: {e}")
        raise


# ── API 호출 ──────────────────────────────────────────────────────────────

def fetch_listings(token, page=1, size=24):
    """# 매물 목록 조회"""
    try:
        r = requests.get(
            LIST_API,
            params={"page": page, "size": size},
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("meta", {}).get("success"):
            return j["data"]
    except Exception as e:
        log.warning(f"목록 조회 실패 (page={page}): {e}")
    return None

def fetch_detail(token, item_id):
    """# 매물 상세 조회"""
    try:
        r = requests.get(
            DETAIL_API.format(id=item_id),
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        r.raise_for_status()
        j = r.json()
        if j.get("meta", {}).get("success"):
            return j["data"]
    except Exception as e:
        log.warning(f"상세 조회 실패 (id={item_id}): {e}")
    return None


# ── 데이터 변환 ───────────────────────────────────────────────────────────

def format_price(trade):
    """# 가격 표시 포맷 생성"""
    trade_type = trade.get("trade_type", "")
    if trade_type == "SALE":
        p = sanitize_number(trade.get("sale_price"))
        return f"매매 {int(p):,}만원" if p else ""
    if trade_type == "RENT":
        dep = sanitize_number(trade.get("deposit"))
        rent = sanitize_number(trade.get("monthly_rent"))
        if dep and rent:
            return f"보증금 {int(dep):,}만원 / 월세 {int(rent):,}만원"
        if dep:
            return f"보증금 {int(dep):,}만원"
    if trade_type == "PRE_SALE":
        p = sanitize_number(trade.get("pre_sale_price"))
        return f"분양가 {int(p):,}만원" if (p and p > 1) else "분양가 협의"
    return ""

def format_move_in(trade):
    """# 입주가능일 포맷"""
    parts = []
    date = trade.get("move_in_available_date") or ""
    if date:
        parts.append(date)
    if trade.get("is_move_in_immediate"):
        parts.append("즉시입주가능")
    if trade.get("is_move_in_adjustable"):
        parts.append("입주일 조율가능")
    return " / ".join(parts) if parts else ""

def format_floor(building):
    """# 해당층 포맷 (지상/지하 + 층수)"""
    floor_no = sanitize_number(building.get("floor_no"))
    is_ground = building.get("is_ground")
    if floor_no is None:
        return ""
    prefix = "지상" if is_ground else "지하"
    return f"{prefix} {int(floor_no)}층"

def enrich_item(raw_list, raw_detail=None):
    """# 팜올 API 데이터 → 대시보드 공통 형식으로 변환 (모든 필드 포함)"""
    d         = raw_detail or raw_list
    trade     = d.get("trade") or {}
    building  = d.get("building") or {}
    operation = d.get("operation") or {}
    location  = d.get("location") or {}
    business  = d.get("business") or {}
    images    = d.get("images") or []
    item_id   = d.get("id") or raw_list.get("id")

    # ── 면적 ──
    exc = sanitize_number(building.get("exclusive_area_m2") or d.get("exclusive_area_m2"))
    sup = sanitize_number(building.get("supply_area_m2") or d.get("supply_area_m2"))
    area_label = f"전용 {exc:.2f}㎡ ({exc/3.305785:.1f}평)" if exc else ""
    area_full  = area_label
    if sup:
        area_full += f" / 공용 {sup:.2f}㎡ (약 {sup/3.305785:.2f}평)"

    # ── 수익 ──
    rx  = sanitize_number(operation.get("monthly_rx_fee_avg") or d.get("monthly_rx_fee_avg"))
    otc = sanitize_number(operation.get("otc_daily_sales_avg") or d.get("otc_daily_sales_avg"))
    sale_count  = f"{int(rx):,}만원/월" if rx else ""
    sale_amount = f"{int(otc):,}만원/일" if otc else ""

    # ── 거래구분 / 상권 / 형태 ──
    trade_type_raw = trade.get("trade_type") or d.get("trade_type", "")
    op_type_raw    = operation.get("operation_type") or d.get("operation_type", "")
    sales_type_raw = operation.get("sales_type") or d.get("sales_type", "")
    trade_area_raw = operation.get("trade_area_type") or d.get("trade_area_type", "")

    gubun_type = f"{TRADE_TYPE_MAP.get(trade_type_raw, trade_type_raw)} / {OP_TYPE_MAP.get(op_type_raw, op_type_raw)}"
    trade_area = AREA_TYPE_MAP.get(trade_area_raw, trade_area_raw)
    is_brok = d.get("is_brokerage") or d.get("broker_listing") or d.get("listing_type") or trade.get("is_brokerage") or operation.get("is_brokerage")
    log.info(f"[DEBUG] id={item_id!r} trade_area={trade_area_raw!r} is_brok={is_brok!r} keys={list(d.keys())[:8]}")
    form_type  = SALES_TYPE_MAP.get(sales_type_raw, sales_type_raw)

    # ── 건축물 정보 ──
    # property_type: trade -> building -> 최상위 순서로 탐색
    property_type_raw = trade.get("property_type") or building.get("property_type") or d.get("property_type") or ""
    building_usage    = PROPERTY_TYPE_MAP.get(property_type_raw, property_type_raw)
    total_floors      = sanitize_number(building.get("total_floors"))
    floor_label       = format_floor(building)
    rooms             = sanitize_number(building.get("rooms"))
    bathroom_type_raw = building.get("bathroom_type") or ""
    bathroom_type     = BATHROOM_TYPE_MAP.get(bathroom_type_raw, bathroom_type_raw)
    bathroom_count    = sanitize_number(building.get("bathroom_count"))
    direction         = sanitize_text(building.get("direction") or "", max_len=20)
    parking_total     = sanitize_number(building.get("parking_total_count"))
    parking_avail     = sanitize_number(building.get("parking_available_count"))
    # approval_date: trade -> building -> 최상위 순서로 탐색
    approval_date     = sanitize_text(trade.get("approval_date") or building.get("approval_date") or d.get("approval_date") or "", max_len=30)
    move_in_label     = format_move_in(trade)

    # ── 조회수 ──
    view_count = sanitize_number(d.get("page_view_count") or d.get("view_count") or 0)

    # ── 이미지 ──
    thumb_url = ""
    for img in images:
        url = sanitize_url(img.get("url") or img.get("image_url") or "")
        if url:
            thumb_url = url
            break

    # ── 연락처 / 담당자 ──
    phone = sanitize_text(business.get("representative_phone_number") or "", max_len=20)
    owner = sanitize_text(business.get("company_name") or "", max_len=100)

    # ── 등록일 ──
    date_str = ""
    created_at = d.get("created_at") or ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            KST = timezone(timedelta(hours=9))
            date_str = dt.astimezone(KST).strftime("%Y.%m.%d")
        except Exception:
            date_str = created_at[:10]

    # ── 태그 ──
    tags_list = []
    if operation.get("is_exclusive") or d.get("is_exclusive"):
        tags_list.append("독점")
    if op_type_raw:
        tags_list.append(OP_TYPE_MAP.get(op_type_raw, op_type_raw))
    if trade_type_raw:
        tags_list.append(TRADE_TYPE_MAP.get(trade_type_raw, trade_type_raw))
    if form_type:
        tags_list.append(form_type)
    if trade_area:
        tags_list.append(trade_area)

    # ── 메모 (전체 정보 텍스트) ──
    addr = sanitize_text(location.get("address") or "", max_len=200)
    parts = []
    if addr:
        parts.append(f"주소: {addr}")
    if area_full:
        parts.append(f"면적: {area_full}")
    maint = sanitize_number(trade.get("maintenance_fee"))
    if maint:
        parts.append(f"관리비: {int(maint):,}만원")
    if sale_count:
        parts.append(f"월조제료: {sale_count}")
    if sale_amount:
        parts.append(f"일반약일매출: {sale_amount}")
    # 건축물 정보 추가
    if building_usage:
        parts.append(f"건물용도: {building_usage}")
    if approval_date:
        parts.append(f"사용승인일: {approval_date}")
    if total_floors:
        parts.append(f"총층: {int(total_floors)}층")
    if floor_label:
        parts.append(f"해당층: {floor_label}")
    if rooms:
        bath_str = f"{bathroom_type}, {int(bathroom_count)}개" if bathroom_count else ""
        parts.append(f"방수: {int(rooms)}개 / 화장실: {bath_str}")
    if parking_total is not None:
        parts.append(f"주차: 총 {int(parking_total)}대 / 가능 {int(parking_avail) if parking_avail else 0}대")
    if direction:
        parts.append(f"방향: {direction}")
    if move_in_label:
        parts.append(f"입주가능일: {move_in_label}")
    if view_count:
        parts.append(f"조회수: {int(view_count)}")
    desc = sanitize_text(d.get("description") or "", max_len=1000)
    if desc:
        parts.append(f"상세: {desc}")

    full_location = sanitize_text(
        location.get("administrative_area_full_name") or d.get("administrative_area_full_name") or "", max_len=100
    )
    region = full_location.split()[0] if full_location else ""

    return {
        "idx":            f"pm_{item_id}",
        "pharmall_id":    item_id,
        "source":         "pharmall",
        "title":          sanitize_text(d.get("title") or "", max_len=200),
        "region":         region,
        "location":       full_location,
        "price":          format_price(trade),
        "phone":          phone,
        "owner":          owner,
        "date":           date_str,
        "area_label":     area_label,
        "area_full":      area_full,
        "trade_area":     trade_area,
        "form_type":      form_type,
        "gubun_type":     gubun_type,
        "sale_count":     sale_count,
        "sale_amount":    sale_amount,
        "thumb_url":      thumb_url,
        "tags":           ", ".join(tags_list),
        "memo":           "\n".join(parts),
        # 건축물 정보
        "building_usage": building_usage,
        "approval_date":  approval_date,
        "total_floors":   int(total_floors) if total_floors else "",
        "floor_label":    floor_label,
        "rooms":          int(rooms) if rooms else "",
        "bathroom":       f"{bathroom_type}, {int(bathroom_count)}개" if bathroom_count else "",
        "parking_total":  int(parking_total) if parking_total else "",
        "parking_avail":  int(parking_avail) if parking_avail else "",
        "direction":      direction,
        "move_in":        move_in_label,
        "view_count":     int(view_count) if view_count else 0,
        "maintenance_fee": f"{int(maint):,}만원" if maint else "",
        "status":         "active",
        "collected_at":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── 메인 크롤 ─────────────────────────────────────────────────────────────

def crawl():
    """# 팜올 전체 크롤링 → {key: item} dict 반환"""
    token = login()

    # 1페이지로 전체 수 파악
    first = fetch_listings(token, page=1, size=24)
    if not first:
        log.error("팜올 목록 조회 실패")
        return {}

    total_pages = first.get("total_pages", 1)
    total_items = first.get("total_items", 0)
    log.info(f"팜올 총 {total_items}건 / {total_pages}페이지")

    # 전체 목록 수집
    all_listings = list(first.get("items", []))
    for page in range(2, total_pages + 1):
        data = fetch_listings(token, page=page, size=24)
        if data:
            all_listings.extend(data.get("items", []))
        time.sleep(0.3)

    log.info(f"목록 수집 완료: {len(all_listings)}건 → 상세 조회 시작")

    # 상세 조회 및 변환
    items = {}
    for listing in all_listings:
        item_id = listing.get("id")
        key     = f"pm_{item_id}"
        detail  = fetch_detail(token, item_id)
        time.sleep(0.3)

        if detail:
            items[key] = enrich_item(listing, detail)
            log.info(f"  ✅ [pm_{item_id}] {items[key]['title']}")
        else:
            items[key] = enrich_item(listing)
            log.warning(f"  ⚠️  [pm_{item_id}] 상세 조회 실패, 목록 데이터만 사용")

    log.info(f"팜올 크롤링 완료: {len(items)}건")
    return items


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result = crawl()
    print(f"\n수집 완료: {len(result)}건")



