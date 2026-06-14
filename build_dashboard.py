# -*- coding: utf-8 -*-
import json, html, pathlib, re, time, logging
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

# Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ¬Ã«Â¡Â¤Ã«ÂÂ¬ Ã¬ÂÂÃ­ÂÂ¬Ã­ÂÂ¸ (Ã¬ÂÂÃ¬ÂÂ¼Ã«Â©Â´ ÃªÂ²Â½ÃªÂ³Â Ã«Â§Â)
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
    # Ã¬ÂÂÃ¬ÂÂÃ¬Â Â Ã¬ÂÂ°ÃªÂ¸Â°: Ã¬ÂÂÃ¬ÂÂ Ã­ÂÂÃ¬ÂÂ¼Ã¬ÂÂ Ã¬Â ÂÃ¬ÂÂ¥ Ã­ÂÂ ÃªÂµÂÃ¬Â²Â´ (Ã¬ÂÂ°ÃªÂ¸Â° Ã«ÂÂÃ¬Â¤Â Ã¬ÂÂÃ«Â¦Â¼ Ã«Â°Â©Ã¬Â§Â)
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
        log.warning(f"  idx={idx} Ã¬ÂÂÃ¬Â²Â­ Ã¬ÂÂ¤Ã­ÂÂ¨: {e}")
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
    # Ã¬ÂÂÃ«ÂÂÃªÂ²Â½Ã«Â¡ÂÃ«Â¥Â¼ Ã¬Â ÂÃ«ÂÂÃªÂ²Â½Ã«Â¡ÂÃ«Â¡Â Ã«Â³ÂÃ­ÂÂ (Ã¬ÂÂ´Ã«Â¯Â¸Ã¬Â§Â Ã­ÂÂÃ¬ÂÂ Ã«Â¬Â¸Ã¬Â Â Ã­ÂÂ´ÃªÂ²Â°)
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
    if item["location"]:     parts.append(f"Ã¬Â£Â¼Ã¬ÂÂ: {item['location']}")
    if item["area_label"]:   parts.append(f"Ã«Â©Â´Ã¬Â Â: {item['area_label']}")
    if item["built"]:        parts.append(f"Ã¬Â¤ÂÃªÂ³Âµ: {item['built']}")
    if item["move_date"]:    parts.append(f"Ã¬ÂÂÃ¬Â£Â¼: {item['move_date']}")
    if item["sale_count"]:   parts.append(f"Ã¬Â²ÂÃ«Â°Â©Ã¬Â¡Â°Ã¬Â Â: {item['sale_count']}")
    if item["sale_amount"]:  parts.append(f"Ã¬ÂÂ¼Ã«Â§Â¤Ã¬Â¶Â: {item['sale_amount']}")
    if item["special_flag"]: parts.append(f"Ã­ÂÂ¹Ã¬ÂÂ´Ã¬ÂÂ¬Ã­ÂÂ­: {item['special_flag']}")
    cp = html_to_text(d.get("content") or "")
    if cp: parts.append(f"Ã¬ÂÂÃ¬ÂÂ¸: {cp}")
    item["memo"] = "\n".join(parts)
    item["collected_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return item

def crawl():
    # Ã¢ÂÂÃ¢ÂÂ Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  Ã­ÂÂ¬Ã«Â¡Â¤Ã«Â§Â Ã¢ÂÂÃ¢ÂÂ
    items = load_items()
    # ÃªÂ¸Â°Ã¬Â¡Â´ Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  Ã­ÂÂ­Ã«ÂªÂ©Ã¬ÂÂ source Ã­ÂÂÃ«ÂÂ Ã¬Â¶ÂÃªÂ°Â
    for k, v in items.items():
        if not str(k).startswith("pm_") and "source" not in v:
            v["source"] = "kpa"

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    kpa_idxs = sorted(int(k) for k in items.keys() if not str(k).startswith("pm_"))
    max_idx = kpa_idxs[-1] if kpa_idxs else 9792
    log.info(f"ÃªÂ¸Â°Ã¬Â¡Â´ Ã­ÂÂ­Ã«ÂªÂ©: {len(items)}ÃªÂ±Â´ | Ã¬ÂµÂÃ«ÂÂ idx: {max_idx}")

    log.info("Ã¢ÂÂÃ¢ÂÂ Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  ÃªÂ¸Â°Ã¬Â¡Â´ Ã­ÂÂ­Ã«ÂªÂ© ÃªÂ°Â±Ã¬ÂÂ  Ã¬Â¤Â...")
    for idx in kpa_idxs:
        key = str(idx)
        if items[key].get("status") == "Ã¬ÂÂ­Ã¬Â Â":
            continue
        d = fetch_detail(idx)
        if d:
            items[key] = enrich(items[key], d)
            items[key]["source"] = "kpa"
            log.info(f"  Ã¢ÂÂ [{idx}] {items[key]['title']}")
        else:
            items[key]["status"] = "Ã¬ÂÂ­Ã¬Â Â"
            items[key]["deleted_at"] = now_str
            log.info(f"  Ã°ÂÂÂÃ¯Â¸Â  [{idx}] Ã¬ÂÂ­Ã¬Â Â ÃªÂ°ÂÃ¬Â§Â")
        time.sleep(DELAY)

    log.info(f"Ã¢ÂÂÃ¢ÂÂ Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  Ã¬ÂÂ ÃªÂ·Â Ã¬ÂÂ¤Ã¬ÂºÂ: {max_idx+1} ~ {max_idx+SCAN_AHEAD}")
    for idx in range(max_idx + 1, max_idx + SCAN_AHEAD + 1):
        key = str(idx)
        if key in items:
            continue
        d = fetch_detail(idx)
        if d:
            items[key] = enrich({"idx": idx}, d)
            items[key]["source"] = "kpa"
            log.info(f"  Ã°ÂÂÂ [{idx}] {items[key]['title']} Ã¬ÂÂ ÃªÂ·Â Ã¬Â¶ÂÃªÂ°Â!")
        time.sleep(DELAY)

    # Ã¢ÂÂÃ¢ÂÂ Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ¬Ã«Â¡Â¤Ã«Â§Â Ã¢ÂÂÃ¢ÂÂ
    if HAS_PHARMALL:
        log.info("Ã¢ÂÂÃ¢ÂÂ Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ¬Ã«Â¡Â¤Ã«Â§Â Ã¬ÂÂÃ¬ÂÂ...")
        try:
            pharmall_items = crawler_pharmall.crawl()
            # ÃªÂ¸Â°Ã¬Â¡Â´ Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ­Ã«ÂªÂ© Ã¬Â ÂÃªÂ±Â° Ã­ÂÂ Ã¬ÂµÂÃ¬ÂÂ Ã¬ÂÂ¼Ã«Â¡Â ÃªÂµÂÃ¬Â²Â´
            for k in [k for k in list(items.keys()) if str(k).startswith("pm_")]:
                del items[k]
            items.update(pharmall_items)
            log.info(f"Ã­ÂÂÃ¬ÂÂ¬ {len(pharmall_items)}ÃªÂ±Â´ Ã«Â³ÂÃ­ÂÂ© Ã¬ÂÂÃ«Â£Â")
        except Exception as e:
            log.error(f"Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ¬Ã«Â¡Â¤Ã«Â§Â Ã¬ÂÂ¤Ã­ÂÂ¨ (Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  Ã«ÂÂ°Ã¬ÂÂ´Ã­ÂÂ°Ã«ÂÂ Ã¬ÂÂ Ã¬Â§Â): {e}")
    else:
        log.warning("crawler_pharmall.py Ã¬ÂÂÃ¬ÂÂ - Ã­ÂÂÃ¬ÂÂ¬ Ã­ÂÂ¬Ã«Â¡Â¤Ã«Â§Â Ã¬ÂÂ¤Ã­ÂÂµ")

    # ── 팜플 크롤링 ──
    if HAS_PHARMPLE:
        log.info("── 팜플 크롤링 시작...")
        try:
            pharmple_items = crawler_pharmple.crawl()
            # 기존 팜플 항목 제거 후 최신으로 교체
            for k in [k for k in list(items.keys()) if str(k).startswith("pp_")]:
                del items[k]
            items.update(pharmple_items)
            log.info(f"팜플 {len(pharmple_items)}건 병합 완료")
        except Exception as e:
            log.error(f"팜플 크롤링 실패 (기존 데이터는 유지): {e}")
    else:
        log.warning("crawler_pharmple.py 없음 - 팜플 크롤링 스킵")

    # Ã¢ÂÂÃ¢ÂÂ Ã¬Â¤ÂÃ«Â³Âµ ÃªÂ°ÂÃ¬Â§Â Ã¢ÂÂÃ¢ÂÂ
    # ÃªÂ¸Â°Ã¬Â¤Â 1 (ÃªÂµÂÃ¬Â°Â¨Ã¬Â¤ÂÃ«Â³Âµ): KPAÃ¢ÂÂÃ­ÂÂÃ¬ÂÂ¬ ÃªÂ°ÂÃ¬ÂÂ Ã¬ÂÂ/ÃªÂµÂ° Ã¬Â£Â¼Ã¬ÂÂ
    # ÃªÂ¸Â°Ã¬Â¤Â 2 (Ã«ÂÂ´Ã«Â¶ÂÃ¬Â¤ÂÃ«Â³Âµ): ÃªÂ°ÂÃ¬ÂÂ Ã¬ÂÂ¬Ã¬ÂÂ´Ã­ÂÂ¸ Ã«ÂÂ´ Ã¬Â ÂÃ­ÂÂÃ«Â²ÂÃ­ÂÂ¸+Ã¬Â§ÂÃ¬ÂÂ­ Ã«ÂÂÃ¬ÂÂ¼
    def _norm_phone(p):
        return re.sub(r'\D', '', str(p or ''))

    active_list = [(k, v) for k, v in items.items() if v.get("status") != "Ã¬ÂÂ­Ã¬Â Â"]
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
            # ÃªÂ¸Â°Ã¬Â¤Â 1: ÃªÂµÂÃ¬Â°Â¨Ã¬Â¤ÂÃ«Â³Âµ - Ã¬ÂÂÃ«Â¡Â Ã«ÂÂ¤Ã«Â¥Â¸ Ã¬ÂÂ¬Ã¬ÂÂ´Ã­ÂÂ¸, ÃªÂ°ÂÃ¬ÂÂ Ã¬ÂÂ/ÃªÂµÂ° Ã¬Â£Â¼Ã¬ÂÂ
            if is_pm1 != is_pm2 and len(ph1) >= 8 and ph1 == ph2:
                _union(k1, k2)
                continue
            # ÃªÂ¸Â°Ã¬Â¤Â 2: Ã«ÂÂ´Ã«Â¶ÂÃ¬Â¤ÂÃ«Â³Âµ - ÃªÂ°ÂÃ¬ÂÂ Ã¬ÂÂ¬Ã¬ÂÂ´Ã­ÂÂ¸, Ã¬Â ÂÃ­ÂÂÃ«Â²ÂÃ­ÂÂ¸+Ã¬Â§ÂÃ¬ÂÂ­ Ã«ÂÂÃ¬ÂÂ¼
            if src1 == src2 and len(ph1) >= 8 and ph1 == ph2 and loc1 and loc1 == loc2:
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
            srcs = set('Ã­ÂÂÃ¬ÂÂ¬' if str(kk).startswith('pm_') else 'KPA' for kk in members)
            kind = 'cross' if len(srcs) > 1 else ('kpa' if 'KPA' in srcs else 'pm')
            v["possible_duplicate"] = True
            v["dup_group"] = gid
            v["dup_kind"] = kind
    for root, (gid, members) in group_map.items():
        titles = [str(items[m].get('title',''))[:15] for m in members]
        srcs = set('Ã­ÂÂÃ¬ÂÂ¬' if str(m).startswith('pm_') else 'KPA' for m in members)
        kind = 'cross' if len(srcs) > 1 else ('kpa' if 'KPA' in srcs else 'pm')
        log.info(f"  Ã¢ÂÂ Ã¯Â¸Â  Ã¬Â¤ÂÃ«Â³ÂµÃ¬ÂÂÃ¬ÂÂ¬[{kind}] {len(members)}ÃªÂ±Â´: {', '.join(titles)}")
    log.info(f"Ã¬Â¤ÂÃ«Â³ÂµÃ¬ÂÂÃ¬ÂÂ¬ Ã¬Â´Â {dup_group_counter}ÃªÂ·Â¸Ã«Â£Â¹")
    save_items(items)
    log.info(f"items.json Ã¬Â ÂÃ¬ÂÂ¥ Ã¬ÂÂÃ«Â£Â (Ã¬Â´Â {len(items)}ÃªÂ±Â´)")
    return items

def build(items):
    active = [v for v in items.values() if v.get("status") != "Ã¬ÂÂ­Ã¬Â Â"]

    # Ã«ÂÂ Ã¬Â§Â ÃªÂ¸Â°Ã¬Â¤Â Ã¬Â ÂÃ«Â Â¬ (Ã«ÂÂ Ã¬ÂÂÃ¬ÂÂ¤ Ã­ÂÂµÃ­ÂÂ©)
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

    # UTC+9 Ã­ÂÂÃªÂµÂ­ Ã¬ÂÂÃªÂ°ÂÃ¬ÂÂ¼Ã«Â¡Â Ã«Â³ÂÃ­ÂÂ
    KST = timezone(timedelta(hours=9))
    updated_at = datetime.now(KST).strftime("%Y.%m.%d %H:%M KST")

    # Ã¬ÂÂÃ¬ÂÂ¤Ã«Â³Â Ã¬Â¹Â´Ã¬ÂÂ´Ã­ÂÂ¸
    kpa_count      = sum(1 for x in active if x.get("source") == "kpa")
    pharmall_count = sum(1 for x in active if x.get("source") == "pharmall")
    dup_count      = sum(1 for x in active if x.get("possible_duplicate"))
    non_dup_count  = len(active) - dup_count
    broker_count   = sum(1 for x in active if x.get("seller_type") == "Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼")
    direct_count   = sum(1 for x in active if x.get("seller_type") == "Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ")

    list_html = []
    for x in active:
        summary  = (x.get("memo") or "")[:120].replace("\n", " ")
        payload  = html.escape(json.dumps(x, ensure_ascii=False))
        thumb    = esc(x.get("thumb_url") or "")
        thumb_tag = f'<img src="{thumb}" style="width:100%;height:120px;object-fit:cover;border-radius:10px;margin-bottom:8px;" onerror="this.style.display=\'none\'">' if thumb else ""

        # Ã¬ÂÂÃ¬ÂÂ¤ Ã«Â±ÂÃ¬Â§Â
        src = x.get("source", "kpa")
        if src == "pharmall":
            src_badge = '<span class="src-badge src-pharmall">Ã­ÂÂÃ¬ÂÂ¬</span>'
        else:
            src_badge = '<span class="src-badge src-kpa">Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â </span>'

        # Ã«Â§Â¤Ã«Â¬Â¼ Ã¬ÂÂ Ã­ÂÂ Ã«Â°Â°Ã¬Â§Â (Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼ / Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ)
        seller = x.get("seller_type", "")
        if seller == "Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼":
            seller_badge = '<span class="src-badge src-broker">Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼</span>'
        elif seller == "Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ":
            seller_badge = '<span class="src-badge src-direct">Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ</span>'
        else:
            seller_badge = ""

        # Ã¬Â¤ÂÃ«Â³Âµ Ã¬ÂÂÃ¬ÂÂ¬ Ã«Â±ÂÃ¬Â§Â
        dup_badge = '<span class="src-badge src-dup">Ã¬Â¤ÂÃ«Â³ÂµÃ¬ÂÂÃ¬ÂÂ¬</span>' if x.get("possible_duplicate") else ""

        is_dup = '1' if x.get("possible_duplicate") else ''
        list_html.append(f"""<button class="item-card" type="button" data-item="{payload}" data-source="{esc(src)}" data-dup="{is_dup}">
  {thumb_tag}
  <div class="item-top"><strong>{esc(x.get("title") or "(Ã¬Â ÂÃ«ÂªÂ©Ã¬ÂÂÃ¬ÂÂ)")}</strong><span>{src_badge}{seller_badge}{dup_badge}</span></div>
  <div class="item-meta">{esc(" / ".join([v for v in [x.get("region",""), x.get("location",""), x.get("date","")] if v]))}</div>
  <div class="item-price">{esc(x.get("price",""))}</div>
  <div class="item-phone">Ã°ÂÂÂ {esc(x.get("phone",""))}</div>
  <div class="item-summary">{esc(summary)}</div>
</button>""")

    tag_html = "".join(f'<button class="chip chip-filter" type="button" data-tag="{esc(t)}">{esc(t)}</button>' for t in tags)
    deleted_count = len([v for v in items.values() if v.get("status") == "Ã¬ÂÂ­Ã¬Â Â"])

    html_out = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ã¬ÂÂ½ÃªÂµÂ­ Ã«Â§Â¤Ã«Â¬Â¼ Ã«ÂÂÃ¬ÂÂÃ«Â³Â´Ã«ÂÂ</title>
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
      <input id="q" class="search" placeholder="Ã°ÂÂÂ Ã¬Â ÂÃ«ÂªÂ©, Ã¬Â£Â¼Ã¬ÂÂ, Ã¬ÂÂ¤Ã«ÂªÂ ÃªÂ²ÂÃ¬ÂÂ">
      <select id="sel-region">
        <option value="">Ã¬Â ÂÃ¬Â²Â´ Ã¬Â§ÂÃ¬ÂÂ­</option>
        {"".join(f'<option value="{esc(r)}">{esc(r)}</option>' for r in regions)}
      </select>
      <div class="row">
        <button class="chip active sort-btn" data-sort="desc" type="button">Ã¬ÂµÂÃ¬ÂÂ Ã¬ÂÂ</button>
        <button class="chip sort-btn" data-sort="asc" type="button">Ã¬ÂÂ¤Ã«ÂÂÃ«ÂÂÃ¬ÂÂ</button>
      </div>
      <h2>Ã¬Â¶ÂÃ¬Â²Â Ã­ÂÂÃ­ÂÂ°</h2>
      <div class="row">
        <button class="chip active src-btn" data-src="" type="button">Ã¬Â ÂÃ¬Â²Â´ ({len(active)})</button>
        <button class="chip src-btn" data-src="kpa" type="button">Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  ({kpa_count})</button>
        <button class="chip src-btn" data-src="pharmall" type="button">Ã­ÂÂÃ¬ÂÂ¬ ({pharmall_count})</button>
      </div>
      <h2>ÃªÂ±Â°Ã«ÂÂ Ã¬ÂÂ Ã­ÂÂ</h2>
      <div class="row">
        <button class="chip active type-btn" data-type="" type="button">Ã¬Â ÂÃ¬Â²Â´ ({len(active)})</button>
        <button class="chip type-btn" data-type="Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼" type="button">Ã°ÂÂÂ¢ Ã¬Â¤ÂÃªÂ°ÂÃ«Â§Â¤Ã«Â¬Â¼ ({broker_count})</button>
        <button class="chip type-btn" data-type="Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ" type="button">Ã°ÂÂ¤Â Ã¬ÂÂ½Ã¬ÂÂ¬Ã¬Â§ÂÃªÂ±Â°Ã«ÂÂ ({direct_count})</button>
      </div>
      <h2>Ã¬Â¤ÂÃ«Â³Âµ Ã­ÂÂÃ­ÂÂ°</h2>
      <div class="row">
        <button class="chip dup-btn" data-dup="show" type="button">Ã¢ÂÂ Ã¯Â¸Â Ã¬Â¤ÂÃ«Â³ÂµÃ¬ÂÂÃ¬ÂÂ¬ ({dup_count})</button>
        <button class="chip dup-btn" data-dup="hide" type="button">Ã¢ÂÂ Ã¬Â¤ÂÃ«Â³Âµ Ã¬Â ÂÃ¬ÂÂ¸ ({non_dup_count}ÃªÂ±Â´)</button>
      </div>
      <h2>Ã¬Â¡Â°Ã¬Â Â/Ã¬Â²ÂÃ«Â°Â© ÃªÂ±Â´Ã¬ÂÂ</h2>
      <div style="padding:2px 0 6px">
        <div class="row" style="gap:6px;align-items:center;flex-wrap:nowrap">
          <input type="number" id="sale-min" placeholder="Ã¬ÂµÂÃ¬ÂÂ" min="0" step="10" style="width:64px;padding:4px 6px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px">
          <span style="color:var(--muted)">~</span>
          <input type="number" id="sale-max" placeholder="Ã¬ÂµÂÃ«ÂÂ" min="0" step="10" style="width:64px;padding:4px 6px;background:var(--card);border:1px solid var(--border);border-radius:8px;color:var(--text);font-size:13px">
          <button class="chip" id="sale-apply" type="button">Ã¬Â ÂÃ¬ÂÂ©</button>
          <button class="chip" id="sale-reset" type="button">Ã¬Â´ÂÃªÂ¸Â°Ã­ÂÂ</button>
        </div>
        <p style="margin:4px 0 0;font-size:11px;color:var(--muted)">Ã¢ÂÂ» ÃªÂ±Â´Ã¬ÂÂ Ã«Â¯Â¸ÃªÂ¸Â°Ã¬ÂÂ¬ Ã«Â§Â¤Ã«Â¬Â¼Ã¬ÂÂ Ã­ÂÂ­Ã¬ÂÂ Ã­ÂÂ¬Ã­ÂÂ¨</p>
      </div>
      <h2>Ã­ÂÂÃªÂ·Â¸ Ã­ÂÂÃ­ÂÂ°</h2>
      <div class="row">{tag_html}</div>
    </div>
    <p style="margin-top:20px;font-size:13px;color:var(--muted)">Ã­ÂÂÃ¬ÂÂ± <strong>{len(active)}</strong>ÃªÂ±Â´ ÃÂ· Ã¬ÂÂ­Ã¬Â Â {deleted_count}ÃªÂ±Â´ ÃÂ· Ã¬Â¤ÂÃ«Â³ÂµÃ¬ÂÂÃ¬ÂÂ¬ {dup_count}ÃªÂ±Â´</p>
    <p class="update-time">Ã¬ÂµÂÃ¬Â¢Â ÃªÂ°Â±Ã¬ÂÂ : {updated_at}</p>
  </aside>

  <section class="content">
    <div class="panel hero">
      <div class="hero-main">
        <h1>Ã°ÂÂÂ Ã¬ÂÂ½ÃªÂµÂ­ Ã«Â§Â¤Ã«Â¬Â¼<br>Ã«ÂÂÃ¬ÂÂÃ«Â³Â´Ã«ÂÂ</h1>
        <p>Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â  + Ã­ÂÂÃ¬ÂÂ¬ Ã«Â¶ÂÃ«ÂÂÃ¬ÂÂ° Ã«Â§Â¤Ã«Â¬Â¼ Ã­ÂÂµÃ­ÂÂ© Ã«ÂªÂ¨Ã«ÂÂÃ­ÂÂ°Ã«Â§Â</p>
        <div class="row" style="margin-top:14px">
          <span class="chip">Ã¬Â´Â <strong id="hero-count">{len(active)}</strong>ÃªÂ±Â´</span>
          <span class="chip">Ã¬Â§ÂÃ¬ÂÂ­ {len(regions)}ÃªÂ°Â</span>
          <span class="chip">Ã°ÂÂÂ 1Ã¬ÂÂÃªÂ°ÂÃ«Â§ÂÃ«ÂÂ¤ Ã¬ÂÂÃ«ÂÂÃªÂ°Â±Ã¬ÂÂ </span>
        </div>
      </div>
      <div class="stats">
        <div class="stat"><div class="k">Ã¬ÂÂ ÃªÂ·ÂÃ¬ÂÂ½ÃªÂµÂ­</div><div class="v" id="stat-new">0</div></div>
        <div class="stat"><div class="k">Ã¬ÂÂ­Ã¬ÂÂ¸ÃªÂ¶Â/Ã¬ÂÂÃ¬ÂÂ</div><div class="v" id="stat-near">0</div></div>
        <div class="stat"><div class="k">Ã¬ÂÂ°Ã«ÂÂ½Ã¬Â²Â Ã«Â³Â´Ã¬ÂÂ </div><div class="v" id="stat-phone">0</div></div>
        <div class="stat"><div class="k">Ã¬Â¦ÂÃ¬ÂÂ Ã¬ÂÂÃ¬Â£Â¼</div><div class="v" id="stat-fast">0</div></div>
      </div>
    </div>

    <div class="main">
      <div class="panel list" id="list">
        {"".join(list_html) if list_html else '<div class="empty">Ã«ÂÂ°Ã¬ÂÂ´Ã­ÂÂ° Ã¬ÂÂÃ¬ÂÂ</div>'}
      </div>
      <div class="panel detail" id="detail-panel">
        <div class="empty" id="detail-empty">Ã¢ÂÂ Ã¬ÂÂ¼Ã¬ÂªÂ½ Ã«ÂªÂ©Ã«Â¡ÂÃ¬ÂÂÃ¬ÂÂ Ã«Â§Â¤Ã«Â¬Â¼Ã¬ÂÂ Ã¬ÂÂ Ã­ÂÂÃ­ÂÂÃ¬ÂÂ¸Ã¬ÂÂ</div>
        <div id="detail-content" style="display:none">
          <img id="d-img" class="detail-img" src="" alt="" style="display:none">
          <h2 id="d-title" style="margin:0 0 6px"></h2>
          <div id="d-sub" class="item-meta" style="margin-bottom:10px"></div>
          <div class="badges" id="d-badges"></div>
          <div class="grid4">
            <div class="info"><div class="k">Ã«Â§Â¤Ã«Â¬Â¼ÃªÂµÂ¬Ã«Â¶Â</div><div class="v" id="d-gubun"></div></div>
            <div class="info"><div class="k">Ã¬ÂÂÃªÂ¶Â</div><div class="v" id="d-trade"></div></div>
            <div class="info"><div class="k">Ã­ÂÂÃ­ÂÂ</div><div class="v" id="d-category"></div></div>
            <div class="info"><div class="k">Ã«Â©Â´Ã¬Â Â</div><div class="v" id="d-area"></div></div>
            <div class="info"><div class="k">Ã¬ÂÂÃ¬Â¡Â°Ã¬Â ÂÃ«Â£Â</div><div class="v" id="d-sale-count"></div></div>
            <div class="info"><div class="k">1Ã¬ÂÂ¼Ã«Â§Â¤Ã¬Â¶Â</div><div class="v" id="d-sale-amount"></div></div>
            <div class="info"><div class="k">Ã¬ÂÂÃ¬Â£Â¼ÃªÂ°ÂÃ«ÂÂ¥Ã¬ÂÂ¼</div><div class="v" id="d-move"></div></div>
            <div class="info"><div class="k">ÃªÂ´ÂÃ«Â¦Â¬Ã«Â¹Â</div><div class="v" id="d-maint"></div></div>
            <div class="info"><div class="k">Ã°ÂÂÂ Ã¬ÂÂ°Ã«ÂÂ½Ã¬Â²Â</div><div class="v" id="d-phone"></div></div>
            <div class="info"><div class="k">Ã°ÂÂÂ¤ Ã«ÂÂ´Ã«ÂÂ¹Ã¬ÂÂ</div><div class="v" id="d-owner"></div></div>
            <div class="info"><div class="k">Ã­ÂÂ¹Ã¬ÂÂ´Ã¬ÂÂ¬Ã­ÂÂ­</div><div class="v" id="d-special"></div></div>
            <div class="info"><div class="k">Ã°ÂÂÂ Ã¬Â§ÂÃ¬ÂÂ­</div><div class="v" id="d-region"></div></div>
          </div>
          <div id="d-pharmall-section" style="display:none;margin-top:14px">
            <div style="font-size:13px;color:var(--muted);margin-bottom:10px;font-weight:600;border-bottom:1px solid rgba(255,255,255,.1);padding-bottom:8px">Ã°ÂÂÂ¢ ÃªÂ±Â´Ã¬Â¶ÂÃ«Â¬Â¼ Ã¬Â ÂÃ«Â³Â´</div>
            <div class="grid4" id="d-building-grid"></div>
            <div id="d-viewcount-row" style="margin-top:10px;font-size:13px;color:var(--muted)"></div>
          </div>
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
  document.getElementById('d-title').textContent = txt(item.title) || 'Ã¬Â ÂÃ«ÂªÂ©Ã¬ÂÂÃ¬ÂÂ';
  document.getElementById('d-sub').textContent = [item.region, item.location, item.date ? 'Ã«ÂÂ±Ã«Â¡ÂÃ¬ÂÂ¼ ' + item.date : ''].filter(Boolean).join(' ÃÂ· ');
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
  // Ã¬ÂÂÃ¬Â£Â¼ÃªÂ°ÂÃ«ÂÂ¥Ã¬ÂÂ¼: Ã­ÂÂÃ¬ÂÂ¬Ã¬ÂÂ move_in, Ã¬ÂÂ½Ã¬ÂÂ¬ÃªÂ³ÂµÃ«Â¡Â Ã¬ÂÂ move_date
  document.getElementById('d-move').textContent = txt(item.move_in || item.move_date) || '-';
  document.getElementById('d-memo').textContent = txt(item.memo);
  const img = document.getElementById('d-img');
  if (item.thumb_url) {{
    img.src = item.thumb_url; img.style.display = 'block';
    img.onerror = () => {{ img.style.display = 'none'; }};
  }} else {{ img.style.display = 'none'; }}
  // Ã«Â±ÂÃ¬Â§Â
  const badges = document.getElementById('d-badges');
  badges.innerHTML = '';
  [item.price, item.area_full || item.area_label, item.move_in || item.move_date, item.gubun_type].filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge'; s.textContent = v; badges.appendChild(s);
  }});
  txt(item.tags).split(',').map(s => s.trim()).filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge tag'; s.textContent = v; badges.appendChild(s);
  }});
  // Ã­ÂÂÃ¬ÂÂ¬ Ã¬Â ÂÃ¬ÂÂ©: ÃªÂ±Â´Ã¬Â¶ÂÃ«Â¬Â¼ Ã¬Â ÂÃ«Â³Â´ Ã¬ÂÂ¹Ã¬ÂÂ
  const pmSection = document.getElementById('d-pharmall-section');
  if (item.source === 'pharmall') {{
    pmSection.style.display = 'block';
    const bg = document.getElementById('d-building-grid');
    bg.innerHTML = '';
    const bfields = [
      ['ÃªÂ±Â´Ã«Â¬Â¼Ã¬ÂÂ©Ã«ÂÂ', item.building_usage],
      ['Ã¬ÂÂ¬Ã¬ÂÂ©Ã¬ÂÂ¹Ã¬ÂÂ¸Ã¬ÂÂ¼', item.approval_date],
      ['Ã¬Â´ÂÃ¬Â¸Âµ', item.total_floors ? item.total_floors + 'Ã¬Â¸Âµ' : ''],
      ['Ã­ÂÂ´Ã«ÂÂ¹Ã¬Â¸Âµ', item.floor_label],
      ['Ã«Â°Â©Ã¬ÂÂ', item.rooms ? item.rooms + 'ÃªÂ°Â' : ''],
      ['Ã­ÂÂÃ¬ÂÂ¥Ã¬ÂÂ¤', item.bathroom],
      ['Ã¬Â´ÂÃ¬Â£Â¼Ã¬Â°Â¨', item.parking_total ? item.parking_total + 'Ã«ÂÂ' : ''],
      ['ÃªÂ°ÂÃ«ÂÂ¥Ã¬Â£Â¼Ã¬Â°Â¨', item.parking_avail ? item.parking_avail + 'Ã«ÂÂ' : ''],
      ['Ã«Â°Â©Ã­ÂÂ¥', item.direction],
      ['ÃªÂ´ÂÃ«Â¦Â¬Ã«Â¹Â', item.maintenance_fee],
      ['Ã¬ÂÂÃ¬Â£Â¼ÃªÂ°ÂÃ«ÂÂ¥Ã¬ÂÂ¼', item.move_in],
      ['Ã¬Â¡Â°Ã­ÂÂÃ¬ÂÂ', item.view_count ? item.view_count + 'Ã­ÂÂ' : ''],
    ];
    bfields.filter(f => f[1]).forEach(f => {{
      bg.innerHTML += '<div class="info"><div class="k">' + f[0] + '</div><div class="v">' + txt(f[1]) + '</div></div>';
    }});
    document.getElementById('d-viewcount-row').textContent = item.view_count ? 'Ã°ÂÂÂ Ã¬Â¡Â°Ã­ÂÂÃ¬ÂÂ ' + item.view_count + 'Ã­ÂÂ' : '';
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
  document.getElementById('stat-new').textContent = all.filter(x => /Ã¬ÂÂ ÃªÂ·Â/.test(txt(x.tags)+txt(x.title))).length;
  document.getElementById('stat-near').textContent = all.filter(x => /Ã¬ÂÂ­Ã¬ÂÂ¸ÃªÂ¶Â|Ã¬ÂÂÃ¬ÂÂÃ¬ÂÂ¸ÃªÂ·Â¼|Ã¬Â¢ÂÃ«Â³Â|Ã«Â¬Â¸Ã¬Â Â/.test(txt(x.tags)+txt(x.memo)+txt(x.title))).length;
  document.getElementById('stat-phone').textContent = all.filter(x => txt(x.phone)).length;
  document.getElementById('stat-fast').textContent = all.filter(x => /Ã«Â°ÂÃ«Â¡Â|Ã¬Â¦ÂÃ¬ÂÂ/.test(txt(x.move_date)+txt(x.move_in)+txt(x.memo))).length;
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

    # Ã¢ÂÂÃ¢ÂÂ HTML Ã­ÂÂÃ¬ÂÂ¼ Ã¬Â ÂÃ¬ÂÂ¥ Ã¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂÃ¢ÂÂ
    DOCS_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOCS_PATH.write_text(html_out, encoding='utf-8')
    log.info(f"HTML Ã¬Â ÂÃ¬ÂÂ¥ Ã¬ÂÂÃ«Â£Â: {DOCS_PATH}")

if __name__ == "__main__":
    import sys
    if "--build-only" in sys.argv:
        log.info("Ã«Â¹ÂÃ«ÂÂ Ã¬Â ÂÃ¬ÂÂ© Ã«ÂªÂ¨Ã«ÂÂ")
        items = load_items()
    else:
        items = crawl()
    build(items)
    log.info("Ã¬ÂÂÃ«Â£Â!")
