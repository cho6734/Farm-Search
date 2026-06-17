# -*- coding: utf-8 -*-
import json, html, pathlib, re, time, logging
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# 팜올 크롤러 임포트 (없으면 경고만)
try:
    import crawler_pharmall
    HAS_PHARMALL = True
except ImportError:
    HAS_PHARMALL = False

# 팜플 크롤러 임포트 (없으면 경고만)
try:
    import crawler_pharmple
    HAS_PHARMPLE = True
except ImportError:
    HAS_PHARMPLE = False

# 데일리팜 크롤러 임포트 (없으면 경고만)
try:
    import crawler_dailypharm
    HAS_DAILYPHARM = True
except ImportError:
    HAS_DAILYPHARM = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT       = pathlib.Path(__file__).resolve().parent
ITEMS_PATH = ROOT / "data" / "items.json"
DOCS_PATH  = ROOT / "docs" / "index.html"
BASE_URL   = "https://svc.kpanews.co.kr/jobs/estate/detail?idx={idx}"
SCAN_AHEAD = 100
DELAY      = 0.35

def load_items():
    if not ITEMS_PATH.exists():
        return {}
    try:
        raw = json.loads(ITEMS_PATH.read_text(encoding="utf-8-sig"))
        return raw if isinstance(raw, dict) else {str(x["idx"]): x for x in raw}
    except Exception:
        return {}

def save_items(items):
    # 원자적 쓰기: 임시 파일에 저장 후 교체 (쓰기 도중 잘림 방지)
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ITEMS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(ITEMS_PATH)

def html_to_text(s):
    s = s or ""
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p>",      "\n", s, flags=re.I)
    s = re.sub(r"&nbsp;",    " ",  s, flags=re.I)
    s = re.sub(r"<[^>]+>",   " ",  s)
    s = re.sub(r"[ \t]+",    " ",  s)
    s = re.sub(r"\n{3,}", "\n\n",  s)
    return s.strip()

def esc(v):
    return html.escape(str(v or ""))

def fetch_detail(idx):
    try:
        r = requests.get(BASE_URL.format(idx=idx), timeout=10)
        r.raise_for_status()
        j = r.json()
        if j.get("rs_code") == "succ" and j.get("data") and j["data"].get("idx"):
            return j["data"]
    except Exception as e:
        log.warning(f"  idx={idx} 요청 실패: {e}")
    return None

def enrich(item, d):
    item["title"]        = d.get("title") or item.get("title") or ""
    item["region"]       = d.get("region_label") or (d.get("addr","").split()[0] if d.get("addr") else "") or item.get("region","")
    item["location"]     = d.get("addr") or item.get("location","")
    item["price"]        = d.get("price_label") or item.get("price","")
    item["phone"]        = d.get("phone") or item.get("phone","")
    item["date"]         = d.get("list_date_disp") or item.get("date","")
    item["area_label"]   = d.get("area_label") or item.get("area_label","")
    item["move_date"]    = d.get("move_date") or item.get("move_date","")
    item["built"]        = d.get("built_label") or item.get("built","")
    item["owner"]        = d.get("owner_label") or d.get("charge_name") or item.get("owner","")
    item["gubun_type"]   = d.get("gubun_type_label") or d.get("gubun_label") or item.get("gubun_type","")
    # 상대경로를 절대경로로 변환 (이미지 표시 문제 해결)
    _thumb = d.get("thumb_url") or item.get("thumb_url","")
    if _thumb and _thumb.startswith("/"):
        _thumb = "https://svc.kpanews.co.kr" + _thumb
    item["thumb_url"] = _thumb
    item["sale_count"]   = d.get("sale_count") or item.get("sale_count","")
    item["sale_amount"]  = d.get("sale_amount") or item.get("sale_amount","")
    item["special_flag"] = d.get("special_flag") or item.get("special_flag","")
    item["tags"]         = ", ".join(d.get("tag_list") or []) or item.get("tags","")
    item["trade_area"]   = d.get("trade_flag_label") or d.get("category_label") or item.get("trade_area","")
    item["status"]       = "active"
    parts = []
    if item["location"]:     parts.append(f"주소: {item['location']}")
    if item["area_label"]:   parts.append(f"면적: {item['area_label']}")
    if item["built"]:        parts.append(f"준공: {item['built']}")
    if item["move_date"]:    parts.append(f"입주: {item['move_date']}")
    if item["sale_count"]:   parts.append(f"처방조제: {item['sale_count']}")
    if item["sale_amount"]:  parts.append(f"일매출: {item['sale_amount']}")
    if item["special_flag"]: parts.append(f"특이사항: {item['special_flag']}")
    cp = html_to_text(d.get("content") or "")
    if cp: parts.append(f"상세: {cp}")
    item["memo"] = "\n".join(parts)
    item["collected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return item

def crawl():
    # ── 약사공론 크롤링 ──
    items = load_items()
    # 기존 약사공론 항목에 source 필드 추가
    for k, v in items.items():
        if str(k).isdigit() and "source" not in v:
            v["source"] = "kpa"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    kpa_idxs = sorted(int(k) for k in items.keys() if str(k).isdigit())
    max_idx = kpa_idxs[-1] if kpa_idxs else 9792
    log.info(f"기존 항목: {len(items)}건 | 최대 idx: {max_idx}")

    log.info("── 약사공론 기존 항목 갱신 중...")
    # [개선] 삭제 항목까지 모두 재조회 → 정상 매물 자동 복구.
    #        + 일괄삭제 방지 안전장치: 실패율이 높으면(원본 장애로 판단) 삭제 처리를 보류한다.
    kpa_results = {}
    kpa_fail = 0
    for idx in kpa_idxs:
        d = fetch_detail(idx)
        kpa_results[str(idx)] = d
        if not d:
            kpa_fail += 1
        time.sleep(DELAY)
    kpa_total = len(kpa_idxs)
    # 안전장치: 절반 이상 실패하면 원본(svc.kpanews) 장애로 간주 → 삭제하지 않고 기존 데이터 유지
    kpa_outage = (kpa_total > 0) and (kpa_fail / kpa_total > 0.5)
    if kpa_outage:
        log.warning(f"⚠️ 약사공론 응답 실패율 높음({kpa_fail}/{kpa_total}) → 원본 장애로 판단, 삭제 보류(기존 데이터 유지)")
    for idx in kpa_idxs:
        key = str(idx)
        d = kpa_results.get(key)
        if d:
            items[key] = enrich(items[key], d)
            items[key]["source"] = "kpa"
            # 정상 조회 → 활성으로 복구(과거에 잘못 삭제된 항목도 되살림)
            if items[key].get("status") == "삭제":
                log.info(f"  ♻️  [{idx}] 복구: {items[key].get('title','')}")
                items[key].pop("deleted_at", None)
            items[key]["status"] = "active"
            log.info(f"  ✅ [{idx}] {items[key].get('title','')}")
        elif kpa_outage:
            # 장애 시: 아무 것도 하지 않고 기존 상태 유지(일괄삭제 방지)
            pass
        else:
            # 개별 실패(원본에서 실제로 내려간 매물) → 삭제 처리
            if items[key].get("status") != "삭제":
                items[key]["status"] = "삭제"
                items[key]["deleted_at"] = now_str
                log.info(f"  🗑️  [{idx}] 삭제 감지")

    log.info(f"── 약사공론 신규 스캔: {max_idx+1} ~ {max_idx+SCAN_AHEAD}")
    for idx in range(max_idx + 1, max_idx + SCAN_AHEAD + 1):
        key = str(idx)
        if key in items:
            continue
        d = fetch_detail(idx)
        if d:
            items[key] = enrich({"idx": idx}, d)
            items[key]["source"] = "kpa"
            log.info(f"  🆕 [{idx}] {items[key]['title']} 신규 추가!")
        time.sleep(DELAY)

    # ── 팜올 크롤링 ──
    if HAS_PHARMALL:
        log.info("── 팜올 크롤링 시작...")
        try:
            pharmall_items = crawler_pharmall.crawl()
            # 기존 팜올 항목 제거 후 최신으로 교체
            for k in [k for k in list(items.keys()) if str(k).startswith("pm_")]:
                del items[k]
            items.update(pharmall_items)
            log.info(f"팜올 {len(pharmall_items)}건 병합 완료")
        except Exception as e:
            log.error(f"팜올 크롤링 실패 (약사공론 데이터는 유지): {e}")
    else:
        log.warning("crawler_pharmall.py 없음 - 팜올 크롤링 스킵")

    # ── 팜플 크롤링 ──
    if HAS_PHARMPLE:
        log.info("── 팜플 크롤링 시작...")
        try:
            pharmple_items = crawler_pharmple.crawl()
            for k in [k for k in list(items.keys()) if str(k).startswith("pp_")]:
                del items[k]
            items.update(pharmple_items)
            log.info(f"팜플 {len(pharmple_items)}건 병합 완료")
        except Exception as e:
            log.error(f"팜플 크롤링 실패 (기존 데이터는 유지): {e}")
    else:
        log.warning("crawler_pharmple.py 없음 - 팜플 크롤링 스킵")

    # ── 데일리팜 크롤링 ──
    if HAS_DAILYPHARM:
        log.info("── 데일리팜 크롤링 시작...")
        try:
            dailypharm_items = crawler_dailypharm.crawl()
            for k in [k for k in list(items.keys()) if str(k).startswith("dp_")]:
                del items[k]
            items.update(dailypharm_items)
            log.info(f"데일리팜 {len(dailypharm_items)}건 병합 완료")
        except Exception as e:
            log.error(f"데일리팜 크롤링 실패 (기존 데이터는 유지): {e}")
    else:
        log.warning("crawler_dailypharm.py 없음 - 데일리팜 크롤링 스킵")

    # ── 중복 감지 ──
    # 기준 1 (교차중복): 전화번호 동일 (KPA↔팜올 등)
    # 기준 2 (내부중복): 같은 사이트 내 전화번호+지역 동일
    # 기준 3 (교차중복·4사 전체): 다른 사이트 + 같은 지역(구)+면적(평) — 팜플·데일리팜 포함
    def _norm_phone(p):
        return re.sub(r'\D', '', str(p or ''))

    # 교차중복(4사 전체)용: 지역(시도+시군구) 키 / 면적(평) 추출
    def _region_key(v):
        loc = re.sub(r'\s+', ' ', str(v.get('location') or v.get('region') or '')).strip()
        toks = loc.split()
        return ''.join(toks[:2]) if toks else ''

    def _area_pyeong(v):
        for f in ('area_full', 'area_label', 'area'):
            s = str(v.get(f) or '')
            m = re.search(r'([\d,]+(?:\.\d+)?)\s*평', s)
            if m:
                try:
                    return int(round(float(m.group(1).replace(',', ''))))
                except Exception:
                    pass
            m2 = re.search(r'([\d,]+(?:\.\d+)?)\s*(?:m2|\u33a1|m\u00b2)', s)
            if m2:
                try:
                    return int(round(float(m2.group(1).replace(',', '')) / 3.3058))
                except Exception:
                    pass
        return None

    def _src_label(k, v):
        s = str(v.get('source') or '')
        if s:
            return s
        kk = str(k)
        return 'pm' if kk.startswith('pm_') else ('pp' if kk.startswith('pp_') else ('dp' if kk.startswith('dp_') else 'kpa'))

    active_list = [(k, v) for k, v in items.items() if v.get("status") != "삭제"]
    # 교차중복용 키 사전 계산 (지역구 + 면적평)
    _rk = {k: _region_key(v) for k, v in active_list}
    _pa = {k: _area_pyeong(v) for k, v in active_list}
    uf = {}
    def _find(x):
        while uf.get(x, x) != x:
            uf[x] = uf.get(uf.get(x, x), uf.get(x, x))
            x = uf.get(x, x)
        return x
    def _union(x, y):
        rx, ry = _find(x), _find(y)
        if rx != ry:
            uf[ry] = rx

    n = len(active_list)
    for i in range(n):
        k1, v1 = active_list[i]
        loc1 = str(v1.get("location") or '').strip()
        ph1  = _norm_phone(v1.get("phone"))
        reg1 = str(v1.get("region") or '').strip()
        src1 = str(v1.get("source") or '')
        is_pm1 = str(k1).startswith('pm_')
        for j in range(i + 1, n):
            k2, v2 = active_list[j]
            src2 = str(v2.get("source") or '')
            is_pm2 = str(k2).startswith('pm_')
            loc2 = str(v2.get("location") or '').strip()
            ph2  = _norm_phone(v2.get("phone"))
            reg2 = str(v2.get("region") or '').strip()
            # 기준 1: 교차중복 - 서로 다른 사이트, 같은 시/군 주소
            if is_pm1 != is_pm2 and len(ph1) >= 8 and ph1 == ph2:
                _union(k1, k2)
                continue
            # 기준 2: 내부중복 - 같은 사이트, 전화번호+지역 동일
            if src1 == src2 and len(ph1) >= 8 and ph1 == ph2 and loc1 and loc1 == loc2:
                _union(k1, k2)
                continue
            # 기준 3: 교차중복(4사 전체) - 다른 사이트 + 같은 지역(구)+면적(평)
            #   전화번호 없는 팜플·데일리팜도 잡기 위함 (지역구+평수 동일 시 동일매물로 간주)
            if src1 != src2:
                rk1, rk2 = _rk.get(k1, ''), _rk.get(k2, '')
                pa1, pa2 = _pa.get(k1), _pa.get(k2)
                if rk1 and rk1 == rk2 and pa1 is not None and pa1 == pa2:
                    _union(k1, k2)

    group_map = {}
    dup_group_counter = 0
    for k, v in active_list:
        root = _find(k)
        if root not in group_map:
            members = [kk for kk, _ in active_list if _find(kk) == root]
            if len(members) >= 2:
                dup_group_counter += 1
                group_map[root] = (dup_group_counter, members)
    for k, v in active_list:
        root = _find(k)
        if root in group_map:
            gid, members = group_map[root]
            srcs = set(_src_label(kk, items[kk]) for kk in members)
            kind = 'cross' if len(srcs) > 1 else 'internal'
            v["possible_duplicate"] = True
            v["dup_group"] = gid
            v["dup_kind"] = kind
    for root, (gid, members) in group_map.items():
        titles = [str(items[m].get('title',''))[:15] for m in members]
        srcs = set(_src_label(m, items[m]) for m in members)
        kind = 'cross' if len(srcs) > 1 else 'internal'
        log.info(f"  ⚠️  중복의심[{kind}] {len(members)}건: {', '.join(titles)}")
    log.info(f"중복의심 총 {dup_group_counter}그룹")
    save_items(items)
    log.info(f"items.json 저장 완료 (총 {len(items)}건)")
    return items

def build(items):
    active = [v for v in items.values() if v.get("status") != "삭제"]

    # 날짜 기준 정렬 (두 소스 통합)
    def sort_key(x):
        d = str(x.get("date") or "")
        return d if d else "0000.00.00"
    active = sorted(active, key=sort_key, reverse=True)

    regions = sorted({str(x.get("region") or "").strip() for x in active if str(x.get("region") or "").strip()})
    all_tags = set()
    for x in active:
        for t in [a.strip() for a in str(x.get("tags") or "").split(",")]:
            if t: all_tags.add(t)
    tags = sorted(all_tags)

    # UTC+9 한국 시간으로 변환
    KST = timezone(timedelta(hours=9))
    updated_at = datetime.now(KST).strftime("%Y.%m.%d %H:%M KST")

    # 소스별 카운트
    kpa_count      = sum(1 for x in active if x.get("source") == "kpa")
    pharmall_count = sum(1 for x in active if x.get("source") == "pharmall")
    pharmple_count = sum(1 for x in active if x.get("source") == "pharmple")
    dailypharm_count = sum(1 for x in active if x.get("source") == "dailypharm")
    dup_count      = sum(1 for x in active if x.get("possible_duplicate"))
    non_dup_count  = len(active) - dup_count
    broker_count   = sum(1 for x in active if x.get("seller_type") == "중개매물")
    direct_count   = sum(1 for x in active if x.get("seller_type") == "약사직거래")

    list_html = []
    for x in active:
        summary  = (x.get("memo") or "")[:120].replace("\n", " ")
        payload  = html.escape(json.dumps(x, ensure_ascii=False))
        thumb    = esc(x.get("thumb_url") or "")
        thumb_tag = f'<img src="{thumb}" style="width:100%;height:120px;object-fit:cover;border-radius:10px;margin-bottom:8px;" onerror="this.style.display=\'none\'">' if thumb else ""

        # 소스 뱃지
        src = x.get("source", "kpa")
        if src == "pharmall":
            src_badge = '<span class="src-badge src-pharmall">팜올</span>'
        elif src == "pharmple":
            src_badge = '<span class="src-badge src-pharmple">팜플</span>'
        elif src == "dailypharm":
            src_badge = '<span class="src-badge src-dailypharm">데일리팜</span>'
        else:
            src_badge = '<span class="src-badge src-kpa">약사공론</span>'

        # 매물 유형 배지 (중개매물 / 약사직거래)
        seller = x.get("seller_type", "")
        if seller == "중개매물":
            seller_badge = '<span class="src-badge src-broker">중개매물</span>'
        elif seller == "약사직거래":
            seller_badge = '<span class="src-badge src-direct">약사직거래</span>'
        else:
            seller_badge = ""

        # 중복 의심 뱃지
        dup_badge = '<span class="src-badge src-dup">중복의심</span>' if x.get("possible_duplicate") else ""

        is_dup = '1' if x.get("possible_duplicate") else ''
        list_html.append(f"""<button class="item-card" type="button" data-item="{payload}" data-source="{esc(src)}" data-dup="{is_dup}">
  {thumb_tag}
  <div class="item-top"><strong>{esc(x.get("title") or "(제목없음)")}</strong><span>{src_badge}{seller_badge}{dup_badge}</span></div>
  <div class="item-meta">{esc(" / ".join([v for v in [x.get("region",""), x.get("location",""), x.get("date","")] if v]))}</div>
  <div class="item-price">{esc(x.get("price",""))}</div>
  <div class="item-phone">📞 {esc(x.get("phone",""))}</div>
  <div class="item-summary">{esc(summary)}</div>
</button>""")

    tag_html = "".join(f'<button class="chip chip-filter" type="button" data-tag="{esc(t)}">{esc(t)}</button>' for t in tags)
    deleted_count = len([v for v in items.values() if v.get("status") == "삭제"])

    html_out = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>약국 매물 대시보드</title>
<style>
:root{{--text:#eef3ff;--muted:#aebce6;--accent:#69a3ff}}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;background:linear-gradient(180deg,#071127 0%,#09183a 100%);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;min-height:100vh}}
.wrap{{display:grid;grid-template-columns:360px 1fr;gap:18px;padding:18px;max-width:1600px;margin:0 auto}}
.panel{{background:rgba(15,27,72,.92);border:1px solid rgba(105,163,255,.25);border-radius:22px;box-shadow:0 10px 30px rgba(0,0,0,.25)}}
.sidebar{{padding:20px;position:sticky;top:18px;height:calc(100vh - 36px);overflow:auto}}
.content{{display:grid;grid-template-rows:auto 1fr;gap:18px;min-width:0}}
.hero{{padding:20px;display:grid;grid-template-columns:1.7fr .9fr;gap:18px}}
.hero-main h1{{margin:0 0 10px;font-size:48px;line-height:1.05;letter-spacing:-.02em}}
.hero-main p{{margin:0;color:var(--muted);font-size:14px}}
.stats{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.stat{{padding:14px;border-radius:18px;background:rgba(11,22,60,.75);border:1px solid rgba(105,163,255,.18)}}
.stat .k{{font-size:13px;color:var(--muted);margin-bottom:6px}}
.stat .v{{font-size:28px;font-weight:800}}
.search,select{{width:100%;background:#09153b;border:1px solid rgba(255,255,255,.18);color:var(--text);border-radius:14px;padding:12px 14px;outline:none;font-size:14px}}
.stack{{display:grid;gap:10px}}
.row{{display:flex;gap:8px;flex-wrap:wrap}}
.chip{{padding:8px 14px;border-radius:999px;border:1px solid rgba(120,160,255,.35);background:transparent;color:var(--text);cursor:pointer;font-size:13px;text-decoration:none;display:inline-block}}
.chip:hover,.chip.active{{background:#1d468b}}
.sidebar h2{{font-size:14px;margin:16px 0 8px;color:var(--muted)}}
.main{{display:grid;grid-template-columns:380px 1fr;gap:18px;min-height:680px}}
.list{{padding:12px;overflow:auto;max-height:calc(100vh - 240px)}}
.detail{{padding:22px;overflow:auto}}
.item-card{{width:100%;text-align:left;background:#08112f;border:1px solid rgba(255,255,255,.12);border-radius:16px;color:var(--text);padding:14px;margin-bottom:10px;cursor:pointer;display:block;transition:border-color .15s}}
.item-card:hover,.item-card.active{{border-color:rgba(105,163,255,.65);background:#0d173f}}
.item-top{{display:flex;justify-content:space-between;gap:8px;margin-bottom:6px}}
.item-top strong{{font-size:16px;line-height:1.3}}
.item-top span{{font-size:12px;color:var(--muted);white-space:nowrap}}
.item-meta,.item-summary{{color:var(--muted);line-height:1.5;font-size:13px}}
.item-price{{margin-top:5px;font-size:14px;color:#69d4ff}}
.item-phone{{margin-top:3px;font-size:13px;color:var(--muted)}}
.badges{{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0 14px}}
.badge{{padding:6px 12px;border-radius:999px;background:#183979;border:1px solid rgba(120,160,255,.35);font-size:13px}}
.badge.tag{{background:#1a4a1a;border-color:rgba(100,220,100,.4);color:#90ee90}}
.grid4{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin:14px 0}}
.info{{padding:14px;border-radius:16px;background:#0b163d;border:1px solid rgba(255,255,255,.12)}}
.info .k{{font-size:12px;color:var(--muted);margin-bottom:6px}}
.info .v{{font-size:16px;font-weight:700;word-break:break-word}}
.memo{{white-space:pre-wrap;line-height:1.9;font-size:16px;margin-top:14px;padding:16px;background:#0b163d;border-radius:14px;border:1px solid rgba(255,255,255,.08)}}
.detail-img{{width:100%;max-height:280px;object-fit:cover;border-radius:14px;margin-bottom:14px}}
.empty{{color:var(--muted);padding:30px;text-align:center}}
.update-time{{font-size:11px;color:var(--muted);margin-top:6px}}
.src-badge{{font-size:11px;padding:2px 7px;border-radius:999px;font-weight:600;margin-left:4px}}
.src-kpa{{background:#1a3a6b;color:#7ab4ff;border:1px solid rgba(120,180,255,.4)}}
.src-pharmall{{background:#1a4a2a;color:#7adf9a;border:1px solid rgba(100,220,120,.4)}}
.src-pharmple{{background:#3a1a5a;color:#c87aff;border:1px solid rgba(180,100,255,.4)}}
.src-dailypharm{{background:#0a3a4a;color:#5ad6e0;border:1px solid rgba(90,210,230,.4)}}
.src-dup{{background:#4a2a00;color:#ffb84d;border:1px solid rgba(255,180,60,.4)}}
.src-broker{{background:#3a1a00;color:#ffaa55;border:1px solid rgba(255,150,50,.4)}}
.src-direct{{background:#0a2a4a;color:#55aaff;border:1px solid rgba(60,150,255,.4)}}
@media(max-width:1100px){{
  .wrap{{grid-template-columns:1fr}}
  .sidebar{{position:relative;top:auto;height:auto}}
  .hero{{grid-template-columns:1fr}}
  .main{{grid-template-columns:1fr}}
  .list{{max-height:none}}
  .grid4{{grid-template-columns:1fr 1fr}}
  .hero-main h1{{font-size:36px}}
}}
</style>
</head>
<body>
<div class="wrap">
  <aside class="panel sidebar">
    <div class="stack">
      <input id="q" class="search" placeholder="🔍 제목, 주소, 설명 검색">
      <select id="sel-region">
        <option value="">전체 지역</option>
        {"".join(f'<option value="{esc(r)}">{esc(r)}</option>' for r in regions)}
      </select>
      <div class="row">
        <button class="chip active sort-btn" data-sort="desc" type="button">최신순</button>
        <button class="chip sort-btn" data-sort="asc" type="button">오래된순</button>
      </div>
      <h2>출처 필터</h2>
      <div class="row">
        <button class="chip active src-btn" data-src="" type="button">전체 ({len(active)})</button>
        <button class="chip src-btn" data-src="kpa" type="button">약사공론 ({kpa_count})</button>
        <button class="chip src-btn" data-src="pharmall" type="button">팜올 ({pharmall_count})</button>
        <button class="chip src-btn" data-src="pharmple" type="button">팜플 ({pharmple_count})</button>
        <button class="chip src-btn" data-src="dailypharm" type="button">데일리팜 ({dailypharm_count})</button>
      </div>
      <h2>거래 유형</h2>
      <div class="row">
        <button class="chip active type-btn" data-type="" type="button">전체 ({len(active)})</button>
        <button class="chip type-btn" data-type="중개매물" type="button">🏢 중개매물 ({broker_count})</button>
        <button class="chip type-btn" data-type="약사직거래" type="button">🤝 약사직거래 ({direct_count})</button>
      </div>
      <h2>중복 필터</h2>
      <div class="row">
        <button class="chip dup-btn" data-dup="show" type="button">⚠️ 중복의심 ({dup_count})</button>
        <button class="chip dup-btn" data-dup="hide" type="button">✅ 중복 제외 ({non_dup_count}건)</button>
      </div>
      <h2>조제/처방 건수</h2>
      <div style="padding:2px 0 6px">
        <div class="row" style="gap:6px;align-items:center;flex-wrap:nowrap">
          <input type="number" id="sale-min" placeholder="최소" min="0" step="10" style="width:64px;padding:4px 6px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px">
          <span style="color:var(--muted)">~</span>
          <input type="number" id="sale-max" placeholder="최대" min="0" step="10" style="width:64px;padding:4px 6px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px">
          <button class="chip" id="sale-apply" type="button">적용</button>
          <button class="chip" id="sale-reset" type="button">초기화</button>
        </div>
        <p style="margin:4px 0 0;font-size:11px;color:var(--muted)">※ 건수 미기재 매물은 항상 포함</p>
      </div>
      <h2>태그 필터</h2>
      <div class="row">{tag_html}</div>
    </div>
    <p style="margin-top:20px;font-size:13px;color:var(--muted)">활성 <strong>{len(active)}</strong>건 · 삭제 {deleted_count}건 · 중복의심 {dup_count}건</p>
    <p class="update-time">최종 갱신: {updated_at}</p>
  </aside>

  <section class="content">
    <div class="panel hero">
      <div class="hero-main">
        <h1>💊 약국 매물<br>대시보드</h1>
        <p>약사공론 + 팜올 부동산 매물 통합 모니터링</p>
        <div class="row" style="margin-top:14px">
          <span class="chip">총 <strong id="hero-count">{len(active)}</strong>건</span>
          <span class="chip">지역 {len(regions)}개</span>
          <span class="chip">🆕 1시간마다 자동갱신</span>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="k">신규약국</div><div class="v" id="stat-new">0</div></div>
        <div class="stat"><div class="k">역세권/의원</div><div class="v" id="stat-near">0</div></div>
        <div class="stat"><div class="k">연락처 보유</div><div class="v" id="stat-phone">0</div></div>
        <div class="stat"><div class="k">즉시 입주</div><div class="v" id="stat-fast">0</div></div>
      </div>
    </div>

    <div class="main">
      <div class="panel list" id="list">
        {"".join(list_html) if list_html else '<div class="empty">데이터 없음</div>'}
      </div>
      <div class="panel detail" id="detail-panel">
        <div class="empty" id="detail-empty">← 왼쪽 목록에서 매물을 선택하세요</div>
        <div id="detail-content" style="display:none">
          <img id="d-img" class="detail-img" src="" alt="" style="display:none">
          <h2 id="d-title" style="margin:0 0 6px"></h2>
          <div id="d-sub" class="item-meta" style="margin-bottom:10px"></div>
          <div class="badges" id="d-badges"></div>
          <div class="grid4">
            <div class="info"><div class="k">매물구분</div><div class="v" id="d-gubun"></div></div>
            <div class="info"><div class="k">상권</div><div class="v" id="d-trade"></div></div>
            <div class="info"><div class="k">형태</div><div class="v" id="d-category"></div></div>
            <div class="info"><div class="k">면적</div><div class="v" id="d-area"></div></div>
            <div class="info"><div class="k">월조제료</div><div class="v" id="d-sale-count"></div></div>
            <div class="info"><div class="k">1일매출</div><div class="v" id="d-sale-amount"></div></div>
            <div class="info"><div class="k">입주가능일</div><div class="v" id="d-move"></div></div>
            <div class="info"><div class="k">관리비</div><div class="v" id="d-maint"></div></div>
            <div class="info"><div class="k">📞 연락처</div><div class="v" id="d-phone"></div></div>
            <div class="info"><div class="k">👤 담당자</div><div class="v" id="d-owner"></div></div>
            <div class="info"><div class="k">특이사항</div><div class="v" id="d-special"></div></div>
            <div class="info"><div class="k">📍 지역</div><div class="v" id="d-region"></div></div>
          </div>
          <div id="d-pharmall-section" style="display:none;margin-top:14px">
            <div style="font-size:13px;color:var(--muted);margin-bottom:10px;font-weight:600;border-bottom:1px solid rgba(255,255,255,.1);padding-bottom:8px">🏢 건축물 정보</div>
            <div class="grid4" id="d-building-grid"></div>
            <div id="d-viewcount-row" style="margin-top:10px;font-size:13px;color:var(--muted)"></div>
          </div>
          <a id="d-link" href="#" target="_blank" rel="noopener noreferrer" style="display:none;margin:12px 0;padding:9px 14px;background:#1d468b;color:#fff;border-radius:10px;text-decoration:none;font-size:13px;font-weight:600">🔗 원본 페이지 새 창으로 열기</a>
          <div class="memo" id="d-memo"></div>
        </div>
      </div>
          <div class="memo" id="d-memo"></div>
        </div>
      </div>
    </div>
  </section>
</div>

<script>
const listEl = document.getElementById('list');
const q = document.getElementById('q');
const selRegion = document.getElementById('sel-region');
let activeTag = '', sortDir = 'desc', activeSrc = '', activeDup = '', activeType = '', saleMin = 0, saleMax = 0;
function txt(v) {{ return (v == null ? '' : String(v)); }}
function setDetail(item) {{
  document.getElementById('detail-empty').style.display = 'none';
  document.getElementById('detail-content').style.display = 'block';
  document.getElementById('d-title').textContent = txt(item.title) || '제목없음';
  document.getElementById('d-sub').textContent = [item.region, item.location, item.date ? '등록일 ' + item.date : ''].filter(Boolean).join(' · ');
  document.getElementById('d-phone').textContent = txt(item.phone) || '-';
  document.getElementById('d-owner').textContent = txt(item.owner) || '-';
  document.getElementById('d-region').textContent = txt(item.region) || '-';
  document.getElementById('d-gubun').textContent = txt(item.gubun_type) || '-';
  document.getElementById('d-trade').textContent = txt(item.trade_area) || '-';
  document.getElementById('d-area').textContent = txt(item.area_full || item.area_label) || '-';
  document.getElementById('d-category').textContent = txt(item.form_type || item.trade_area) || '-';
  document.getElementById('d-sale-count').textContent = txt(item.sale_count) || '-';
  document.getElementById('d-sale-amount').textContent = txt(item.sale_amount) || '-';
  document.getElementById('d-special').textContent = txt(item.special_flag) || '-';
  document.getElementById('d-maint').textContent = txt(item.maintenance_fee) || '-';
  // 입주가능일: 팜올은 move_in, 약사공론은 move_date
  document.getElementById('d-move').textContent = txt(item.move_in || item.move_date) || '-';
  // 메모: HTML 이스케이프 후 URL을 새 창 하이퍼링크로 변환(+줄바꿈 유지)
  (function(){{
    var raw = txt(item.memo);
    var esc = raw.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
    var html = esc.replace(/(https?:\/\/[^\s<]+)/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer" style="color:#69a3ff;word-break:break-all">$1</a>').replace(/\n/g,'<br>');
    document.getElementById('d-memo').innerHTML = html;
    // 원본 페이지 버튼: item.link 있으면 표시
    var dl = document.getElementById('d-link');
    var url = txt(item.link) || (raw.match(/https?:\/\/[^\s]+/) || [''])[0];
    if (url) {{ dl.href = url; dl.style.display = 'inline-block'; }} else {{ dl.style.display = 'none'; }}
  }})();
  const img = document.getElementById('d-img');
  if (item.thumb_url) {{
    img.src = item.thumb_url; img.style.display = 'block';
    img.onerror = () => {{ img.style.display = 'none'; }};
  }} else {{ img.style.display = 'none'; }}
  // 뱃지
  const badges = document.getElementById('d-badges');
  badges.innerHTML = '';
  [item.price, item.area_full || item.area_label, item.move_in || item.move_date, item.gubun_type].filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge'; s.textContent = v; badges.appendChild(s);
  }});
  txt(item.tags).split(',').map(s => s.trim()).filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge tag'; s.textContent = v; badges.appendChild(s);
  }});
  // 건축물/상세 정보 섹션 (상세 데이터가 있는 모든 출처: 팜올·데일리팜 등)
  const pmSection = document.getElementById('d-pharmall-section');
  const roomsStr = item.rooms ? (String(item.rooms).indexOf('개') >= 0 ? item.rooms : item.rooms + '개') : '';
  const parkingStr = item.parking_label
      ? item.parking_label
      : (item.parking_total ? '총 ' + item.parking_total + '대' + (item.parking_avail ? ' / 가능 ' + item.parking_avail + '대' : '') : '');
  const bfields = [
    ['건물용도/종류', item.building_usage],
    ['사용승인일', item.approval_date],
    ['총층', item.total_floors ? item.total_floors + '층' : ''],
    ['층수', item.floor_label],
    ['방수', roomsStr],
    ['화장실', item.bathroom],
    ['주차', parkingStr],
    ['방향', item.direction],
    ['관리비', item.maintenance_fee],
    ['입주가능일', item.move_in || item.move_date],
    ['조회수', item.view_count ? item.view_count + '회' : ''],
  ];
  const shownB = bfields.filter(f => f[1]);
  if (shownB.length) {{
    pmSection.style.display = 'block';
    const bg = document.getElementById('d-building-grid');
    bg.innerHTML = '';
    shownB.forEach(f => {{
      bg.innerHTML += '<div class="info"><div class="k">' + f[0] + '</div><div class="v">' + txt(f[1]) + '</div></div>';
    }});
    document.getElementById('d-viewcount-row').textContent = item.view_count ? '👁 조회수 ' + item.view_count + '회' : '';
  }} else {{
    pmSection.style.display = 'none';
  }}
}}
function applyFilters() {{
  const term = txt(q.value).toLowerCase().trim();
  const allCards = [...listEl.querySelectorAll('.item-card')];
  const visible = [];
  allCards.forEach(c => {{
    const item = JSON.parse(c.dataset.item);
    const hay = [item.title,item.region,item.location,item.memo,item.tags].join(' ').toLowerCase();
    const ok = (!term || hay.includes(term))
      && (!selRegion.value || txt(item.region) === selRegion.value)
      && (!activeTag || txt(item.tags).includes(activeTag))
      && (!activeSrc || txt(item.source) === activeSrc)
      && (activeDup !== 'show' || c.dataset.dup === '1')
      && (activeDup !== 'hide' || c.dataset.dup !== '1')
      && (!activeType || txt(item.seller_type) === activeType)
      && ((() => {{
        if (!saleMin && !saleMax) return true;
        const cnt = parseInt(txt(item.sale_count)) || 0;
        if (!cnt) return true;
        if (saleMin && cnt < saleMin) return false;
        if (saleMax && cnt > saleMax) return false;
        return true;
      }})());
    c.style.display = ok ? '' : 'none';
    if (ok) visible.push(c);
  }});
  visible.sort((a,b) => {{
    const ai = JSON.parse(a.dataset.item), bi = JSON.parse(b.dataset.item);
    const ad = txt(ai.date) || '0000.00.00', bd = txt(bi.date) || '0000.00.00';
    const cmp = sortDir === 'desc' ? bd.localeCompare(ad) : ad.localeCompare(bd);
    if (cmp !== 0) return cmp;
    const aidx = parseInt(ai.idx) || 0, bidx = parseInt(bi.idx) || 0;
    return sortDir === 'desc' ? bidx - aidx : aidx - bidx;
  }});  visible.forEach(c => listEl.appendChild(c));
  allCards.forEach(c => c.classList.remove('active'));
  if (visible.length) {{ visible[0].classList.add('active'); setDetail(JSON.parse(visible[0].dataset.item)); }}
  const all = visible.map(c => JSON.parse(c.dataset.item));
  document.getElementById('stat-new').textContent = all.filter(x => /신규/.test(txt(x.tags)+txt(x.title))).length;
  document.getElementById('stat-near').textContent = all.filter(x => /역세권|의원인근|종병|문전/.test(txt(x.tags)+txt(x.memo)+txt(x.title))).length;
  document.getElementById('stat-phone').textContent = all.filter(x => txt(x.phone)).length;
  document.getElementById('stat-fast').textContent = all.filter(x => /바로|즉시/.test(txt(x.move_date)+txt(x.move_in)+txt(x.memo))).length;
  document.getElementById('hero-count').textContent = visible.length;
}}
listEl.addEventListener('click', e => {{
  const card = e.target.closest('.item-card');
  if (!card) return;
  [...listEl.querySelectorAll('.item-card')].forEach(c => c.classList.remove('active'));
  card.classList.add('active');
  setDetail(JSON.parse(card.dataset.item));
}});
q.addEventListener('input', applyFilters);
selRegion.addEventListener('change', applyFilters);
document.querySelectorAll('.chip-filter').forEach(b => b.addEventListener('click', () => {{
  activeTag = activeTag === b.dataset.tag ? '' : b.dataset.tag;
  document.querySelectorAll('.chip-filter').forEach(x => x.classList.toggle('active', x.dataset.tag === activeTag));
  applyFilters();
}}));
document.querySelectorAll('.sort-btn').forEach(b => b.addEventListener('click', () => {{
  sortDir = b.dataset.sort;
  document.querySelectorAll('.sort-btn').forEach(x => x.classList.toggle('active', x.dataset.sort === sortDir));
  applyFilters();
}}));
document.querySelectorAll('.src-btn').forEach(b => b.addEventListener('click', () => {{
  activeSrc = b.dataset.src;
  document.querySelectorAll('.src-btn').forEach(x => x.classList.toggle('active', x.dataset.src === activeSrc));
  applyFilters();
}}));
document.querySelectorAll('.dup-btn').forEach(b => b.addEventListener('click', () => {{
  activeDup = activeDup === b.dataset.dup ? '' : b.dataset.dup;
  document.querySelectorAll('.dup-btn').forEach(x => x.classList.toggle('active', x.dataset.dup === activeDup));
  applyFilters();
}}));
document.querySelectorAll('.type-btn').forEach(b => b.addEventListener('click', () => {{
  activeType = activeType === b.dataset.type ? '' : b.dataset.type;
  document.querySelectorAll('.type-btn').forEach(x => x.classList.toggle('active', x.dataset.type === activeType));
  applyFilters();
}}));
const saleApply = document.getElementById('sale-apply');
const saleReset = document.getElementById('sale-reset');
if (saleApply) saleApply.addEventListener('click', () => {{
  saleMin = parseInt(document.getElementById('sale-min').value) || 0;
  saleMax = parseInt(document.getElementById('sale-max').value) || 0;
  applyFilters();
}});
if (saleReset) saleReset.addEventListener('click', () => {{
  saleMin = saleMax = 0;
  document.getElementById('sale-min').value = '';
  document.getElementById('sale-max').value = '';
  applyFilters();
}});
applyFilters();
</script>
</body>
</html>"""

    # ── HTML 파일 저장 ──────────────────────────────────────────────────────
    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_PATH.write_text(html_out, encoding='utf-8')
    log.info(f"HTML 저장 완료: {DOCS_PATH}")

if __name__ == "__main__":
    import sys
    if "--build-only" in sys.argv:
        log.info("빌드 전용 모드")
        items = load_items()
    else:
        items = crawl()
    build(items)
    log.info("완료!")
