# -*- coding: utf-8 -*-
import json, html, pathlib, re, time, logging
from datetime import datetime, timezone, timedelta

try:
    import requests
except ImportError:
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

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
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")

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
    items = load_items()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    existing_idxs = sorted(int(k) for k in items.keys())
    max_idx = existing_idxs[-1] if existing_idxs else 9792
    log.info(f"기존 항목: {len(items)}건 | 최대 idx: {max_idx}")

    log.info("── 기존 항목 갱신 중...")
    for idx in existing_idxs:
        key = str(idx)
        if items[key].get("status") == "삭제":
            continue
        d = fetch_detail(idx)
        if d:
            items[key] = enrich(items[key], d)
            log.info(f"  ✅ [{idx}] {items[key]['title']}")
        else:
            items[key]["status"] = "삭제"
            items[key]["deleted_at"] = now_str
            log.info(f"  🗑️  [{idx}] 삭제 감지")
        time.sleep(DELAY)

    log.info(f"── 신규 스캔: {max_idx+1} ~ {max_idx+SCAN_AHEAD}")
    for idx in range(max_idx + 1, max_idx + SCAN_AHEAD + 1):
        key = str(idx)
        if key in items:
            continue
        d = fetch_detail(idx)
        if d:
            items[key] = enrich({"idx": idx}, d)
            log.info(f"  🆕 [{idx}] {items[key]['title']} 신규 추가!")
        time.sleep(DELAY)

    save_items(items)
    log.info(f"items.json 저장 완료 (총 {len(items)}건)")
    return items

def build(items):
    active = [v for v in items.values() if v.get("status") != "삭제"]
    active = sorted(active, key=lambda x: int(x.get("idx", 0)), reverse=True)
    regions = sorted({str(x.get("region") or "").strip() for x in active if str(x.get("region") or "").strip()})
    all_tags = set()
    for x in active:
        for t in [a.strip() for a in str(x.get("tags") or "").split(",")]:
            if t: all_tags.add(t)
    tags = sorted(all_tags)
    # UTC+9 한국 시간으로 변환
    KST = timezone(timedelta(hours=9))
    updated_at = datetime.now(KST).strftime("%Y.%m.%d %H:%M KST")

    list_html = []
    for x in active:
        summary = (x.get("memo") or "")[:120].replace("\n", " ")
        payload  = html.escape(json.dumps(x, ensure_ascii=False))
        thumb    = esc(x.get("thumb_url") or "")
        thumb_tag = f'<img src="{thumb}" style="width:100%;height:120px;object-fit:cover;border-radius:10px;margin-bottom:8px;" onerror="this.style.display=\'none\'">' if thumb else ""
        list_html.append(f"""<button class="item-card" type="button" data-item="{payload}">
  {thumb_tag}
  <div class="item-top"><strong>{esc(x.get("title") or "(제목없음)")}</strong><span>#{esc(x.get("idx"))}</span></div>
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
      <h2>태그 필터</h2>
      <div class="row">{tag_html}</div>
    </div>
    <p style="margin-top:20px;font-size:13px;color:var(--muted)">활성 <strong>{len(active)}</strong>건 · 삭제 {deleted_count}건</p>
    <p class="update-time">최종 갱신: {updated_at}</p>
  </aside>

  <section class="content">
    <div class="panel hero">
      <div class="hero-main">
        <h1>💊 약국 매물<br>대시보드</h1>
        <p>약사공론 부동산 매물 실시간 모니터링</p>
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
            <div class="info"><div class="k">면적</div><div class="v" id="d-area"></div></div>
            <div class="info"><div class="k">준공년월</div><div class="v" id="d-built"></div></div>
            <div class="info"><div class="k">입주가능일</div><div class="v" id="d-move"></div></div>
            <div class="info"><div class="k">형태분류</div><div class="v" id="d-category"></div></div>
            <div class="info"><div class="k">처방조제건수</div><div class="v" id="d-sale-count"></div></div>
            <div class="info"><div class="k">1일매출</div><div class="v" id="d-sale-amount"></div></div>
            <div class="info"><div class="k">특이사항</div><div class="v" id="d-special"></div></div>
            <div class="info"><div class="k">📞 연락처</div><div class="v" id="d-phone"></div></div>
            <div class="info"><div class="k">👤 담당자</div><div class="v" id="d-owner"></div></div>
            <div class="info"><div class="k">📍 지역</div><div class="v" id="d-region"></div></div>
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
let activeTag = '', sortDir = 'desc';
function txt(v) {{ return (v == null ? '' : String(v)); }}
function setDetail(item) {{
  document.getElementById('detail-empty').style.display = 'none';
  document.getElementById('detail-content').style.display = 'block';
  document.getElementById('d-title').textContent = txt(item.title) || '제목없음';
  document.getElementById('d-sub').textContent = [item.region, item.location, item.date ? '등록일 ' + item.date : ''].filter(Boolean).join(' · ');
  document.getElementById('d-phone').textContent = txt(item.phone) || '-';
  document.getElementById('d-owner').textContent = txt(item.owner) || '-';
  document.getElementById('d-built').textContent = txt(item.built) || '-';
  document.getElementById('d-region').textContent = txt(item.region) || '-';
  document.getElementById('d-gubun').textContent = txt(item.gubun_type) || '-';
  document.getElementById('d-trade').textContent = txt(item.trade_area) || '-';
  document.getElementById('d-area').textContent = txt(item.area_label) || '-';
  document.getElementById('d-move').textContent = txt(item.move_date) || '-';
  document.getElementById('d-category').textContent = txt(item.trade_area) || '-';
  document.getElementById('d-sale-count').textContent = txt(item.sale_count) || '-';
  document.getElementById('d-sale-amount').textContent = txt(item.sale_amount) || '-';
  document.getElementById('d-special').textContent = txt(item.special_flag) || '-';
  document.getElementById('d-memo').textContent = txt(item.memo);
  const img = document.getElementById('d-img');
  if (item.thumb_url) {{
    img.src = item.thumb_url; img.style.display = 'block';
    img.onerror = () => {{ img.style.display = 'none'; }};
  }} else {{ img.style.display = 'none'; }}
  const badges = document.getElementById('d-badges');
  badges.innerHTML = '';
  [item.price, item.area_label, item.move_date, item.gubun_type].filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge'; s.textContent = v; badges.appendChild(s);
  }});
  txt(item.tags).split(',').map(s => s.trim()).filter(Boolean).forEach(v => {{
    const s = document.createElement('span'); s.className = 'badge tag'; s.textContent = v; badges.appendChild(s);
  }});
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
      && (!activeTag || txt(item.tags).includes(activeTag));
    c.style.display = ok ? '' : 'none';
    if (ok) visible.push(c);
  }});
  visible.sort((a,b) => {{
    const ai = parseInt(JSON.parse(a.dataset.item).idx);
    const bi = parseInt(JSON.parse(b.dataset.item).idx);
    return sortDir === 'desc' ? bi-ai : ai-bi;
  }});
  visible.forEach(c => listEl.appendChild(c));
  allCards.forEach(c => c.classList.remove('active'));
  if (visible.length) {{ visible[0].classList.add('active'); setDetail(JSON.parse(visible[0].dataset.item)); }}
  const all = visible.map(c => JSON.parse(c.dataset.item));
  document.getElementById('stat-new').textContent   = all.filter(x => /신규/.test(txt(x.tags)+txt(x.title))).length;
  document.getElementById('stat-near').textContent  = all.filter(x => /역세권|의원인근|종병|문전/.test(txt(x.tags)+txt(x.memo)+txt(x.title))).length;
  document.getElementById('stat-phone').textContent = all.filter(x => txt(x.phone)).length;
  document.getElementById('stat-fast').textContent  = all.filter(x => /바로|즉시/.test(txt(x.move_date)+txt(x.memo))).length;
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
applyFilters();
</script>
</body>
</html>"""

    DOCS_PATH.write_text(html_out, encoding="utf-8")
    log.info(f"docs/index.html 빌드 완료 ({len(active)}건)")

if __name__ == "__main__":
    import sys
    if "--build-only" in sys.argv:
        log.info("빌드 전용 모드")
        items = load_items()
    else:
        items = crawl()
    build(items)
    log.info("완료!")
