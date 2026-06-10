# -*- coding: utf-8 -*-
"""
팜올플러스(pharmallplus) 매물 크롤러
API: https://open-api.pharmallplus.com/v1/listings
인증: 이메일/비밀번호 로그인 → JWT access_token (base64 패스워드)
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
AREA_TYPE_MAP  = {"LOCAL": "로컬", "GENERAL": "일반상권"}


# ── 보안: 4단계 살균 함수 ──────────────────────────────────────────────────

def sanitize_text(v, max_len=500):
    """# 1~4단계: HTML태그 제거 → javascript: 제거 → 스크립트키워드 제거 → 길이제한"""
    if v is None:
        return ""
    s = str(v)
    s = re.sub(r"<[^>]+>", "", s)                          # 1단계: HTML 태그 제거
    s = re.sub(r"javascript\s*:", "", s, flags=re.I)        # 2단계: js 스킴 제거
    s = re.sub(r"(on\w+\s*=|<script|</script)", "", s, flags=re.I)  # 3단계: 이벤트핸들러 제거
    return s.strip()[:max_len]                              # 4단계: 길이 제한

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
    """# 팜올 로그인 → access_token 반환 (비밀번호 RSA PKCS#1 v1.5 암호화 필요)"""
    load_env()
    email    = os.environ.get("PHARMALL_EMAIL", "").strip()
    password = os.environ.get("PHARMALL_PASSWORD", "").strip()
    if not email or not password:
        raise ValueError("PHARMALL_EMAIL, PHARMALL_PASSWORD가 .env에 없습니다")

    try:
        # 1단계: 서버에서 RSA 공개키 조회
        pk_r = requests.get("https://api.pharmallplus.com/v1/users/public-key", timeout=10)
        pk_r.raise_for_status()
        raw_pem = pk_r.json()["data"]["public_key"]
        log.info("팜올 공개키 조회 성공")

        # 2단계: RSA PKCS#1 v1.5로 비밀번호 암호화
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        pub_key = serialization.load_pem_public_key(fix_pem(raw_pem).encode())
        encrypted = pub_key.encrypt(password.encode("utf-8"), asym_padding.PKCS1v15())
        pw_encrypted = base64.b64encode(encrypted).decode("utf-8")

        # 3단계: 로그인 요청
        r = requests.post(LOGIN_API, json={"email": email, "password": pw_encrypted}, timeout=10)
        if r.status_code != 200:
            log.error(f"팜올 로그인 실패 응답: {r.status_code} / 본문: {r.text[:500]}")
        r.raise_for_status()
        j = r.json()
        if j.get("meta", {}).get("success"):
            log.info("팜올 로그인 성공")
            return j["data"]["access_token"]
        raise ValueError(f"로그인 실패: {j.get('meta', {}).get('message')}")
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

def enrich_item(raw_list, raw_detail=None):
    """# 팜올 API 데이터 → 대시보드 공통 형식으로 변환 (보안 살균 포함)"""
    d         = raw_detail or raw_list
    trade     = d.get("trade") or {}
    building  = d.get("building") or {}
    operation = d.get("operation") or {}
    location  = d.get("location") or {}
    business  = d.get("business") or {}
    images    = d.get("images") or []
    item_id   = d.get("id") or raw_list.get("id")

    # 면적
    exc = sanitize_number(building.get("exclusive_area_m2") or d.get("exclusive_area_m2"))
    area_label = f"전용 {exc:.2f}㎡ ({exc/3.305785:.1f}평)" if exc else ""

    # 조제료 / 매출
    rx  = sanitize_number(operation.get("monthly_rx_fee_avg") or d.get("monthly_rx_fee_avg"))
    otc = sanitize_number(operation.get("otc_daily_sales_avg") or d.get("otc_daily_sales_avg"))
    sale_count  = f"{int(rx):,}만원/월" if rx else ""
    sale_amount = f"{int(otc):,}만원/일" if otc else ""

    # 거래구분
    trade_type_raw = trade.get("trade_type") or d.get("trade_type", "")
    op_type_raw    = operation.get("operation_type") or d.get("operation_type", "")
    gubun_type     = f"{TRADE_TYPE_MAP.get(trade_type_raw, trade_type_raw)} / {OP_TYPE_MAP.get(op_type_raw, op_type_raw)}"
    trade_area_raw = operation.get("trade_area_type") or d.get("trade_area_type", "")
    trade_area     = AREA_TYPE_MAP.get(trade_area_raw, trade_area_raw)

    # 이미지 (화이트리스트 검증)
    thumb_url = ""
    for img in images:
        url = sanitize_url(img.get("url") or img.get("image_url") or "")
        if url:
            thumb_url = url
            break

    # 연락처 / 담당자
    phone = sanitize_text(business.get("representative_phone_number") or "", max_len=20)
    owner = sanitize_text(business.get("company_name") or "", max_len=100)

    # 등록일 (KST 변환)
    date_str = ""
    created_at = d.get("created_at") or ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            KST = timezone(timedelta(hours=9))
            date_str = dt.astimezone(KST).strftime("%Y.%m.%d")
        except Exception:
            date_str = created_at[:10]

    # 태그
    tags_list = []
    if operation.get("is_exclusive") or d.get("is_exclusive"):
        tags_list.append("독점")
    if op_type_raw:
        tags_list.append(OP_TYPE_MAP.get(op_type_raw, op_type_raw))
    if trade_type_raw:
        tags_list.append(TRADE_TYPE_MAP.get(trade_type_raw, trade_type_raw))

    # 메모
    parts = []
    addr = sanitize_text(location.get("address") or "", max_len=200)
    if addr:           parts.append(f"주소: {addr}")
    if area_label:     parts.append(f"면적: {area_label}")
    maint = sanitize_number(trade.get("maintenance_fee"))
    if maint:          parts.append(f"관리비: {int(maint):,}만원")
    if sale_count:     parts.append(f"월조제료: {sale_count}")
    if sale_amount:    parts.append(f"일반약일매출: {sale_amount}")
    desc = sanitize_text(d.get("description") or "", max_len=1000)
    if desc:           parts.append(f"상세: {desc}")

    full_location = sanitize_text(
        location.get("administrative_area_full_name") or d.get("administrative_area_full_name") or "", max_len=100
    )
    region = full_location.split()[0] if full_location else ""

    return {
        "idx":          f"pm_{item_id}",
        "pharmall_id":  item_id,
        "source":       "pharmall",
        "title":        sanitize_text(d.get("title") or "", max_len=200),
        "region":       region,
        "location":     full_location,
        "price":        format_price(trade),
        "phone":        phone,
        "owner":        owner,
        "date":         date_str,
        "area_label":   area_label,
        "trade_area":   trade_area,
        "gubun_type":   gubun_type,
        "sale_count":   sale_count,
        "sale_amount":  sale_amount,
        "thumb_url":    thumb_url,
        "tags":         ", ".join(tags_list),
        "memo":         "\n".join(parts),
        "status":       "active",
        "collected_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    }


# ── 메인 크롤 ─────────────────────────────────────────────────────────────

def crawl():
    """# 팜올 전체 크롤링 → {key: item} dict 반환"""
    token = login()

    # 1페이지로 전체 페이지 수 파악
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
