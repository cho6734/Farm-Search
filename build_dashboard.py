import json, html, time, pathlib, requests
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "state.json"
DOCS_PATH = ROOT / "docs" / "index.html"
DETAIL_URL = "https://svc.kpanews.co.kr/jobs/estate/detail?idx={idx}"

def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))

def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8-sig")

def fetch_item(idx):
    url = DETAIL_URL.format(idx=idx)
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent":"Mozilla/5.0"})
        if r.status_code != 200:
            return None
        data = r.json()
        if not data:
            return None
        if isinstance(data, dict):
            text = json.dumps(data, ensure_ascii=False)
            if "idx" not in text and "title" not in text and "subject" not in text:
                return None
            return data
    except Exception:
        return None
    return None

def norm(item, idx):
    def pick(*keys):
        for k in keys:
            v = item.get(k)
            if v not in (None, "", []):
                return v
        return ""
    return {
        "idx": idx,
        "title": str(pick("title","subject","sj")),
        "region": str(pick("region","area","sido","addr1")),
        "location": str(pick("location","addr","address","addr2")),
        "price": str(pick("price","deposit","amount")),
        "rent": str(pick("rent","monthly_rent","monthPrice")),
        "phone": str(pick("phone","tel","mobile")),
        "memo": str(pick("memo","content","desc","description")),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "url": f"https://www.kpanews.co.kr/"
    }

def scan(state):
    start = int(state["last_id"]) + 1
    end = start + int(state["scan_window"]) - 1
    miss = 0
    found = []

    for idx in range(start, end + 1):
        item = fetch_item(idx)
        if item is None:
            miss += 1
            if miss >= int(state["stop_after_miss"]):
                break
            continue
        miss = 0
        found.append(norm(item, idx))
        state["last_id"] = idx

    state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return found, state

def render(items, state):
    cards = []
    for x in sorted(items, key=lambda z: z["idx"], reverse=True):
        tags = " ".join(filter(None, [x["region"], x["location"]]))
        cards.append(f"""
        <article class="card" data-title="{html.escape(x['title'])}" data-tags="{html.escape(tags)}" data-idx="{x['idx']}">
          <div class="top">
            <h3>{html.escape(x['title'] or '(제목없음)')}</h3>
            <span class="idx">#{x['idx']}</span>
          </div>
          <p>{html.escape(' / '.join([v for v in [x['region'], x['location']] if v]))}</p>
          <p>보증금: {html.escape(x['price'])} / 월세: {html.escape(x['rent'])}</p>
          <p>연락처: {html.escape(x['phone'])}</p>
          <p class="memo">{html.escape(x['memo'][:200])}</p>
        </article>
        """)

    html_doc = f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>약사공론 매물 대시보드</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f7f7f7;color:#111}}
.wrap{{max-width:1100px;margin:0 auto;padding:20px}}
header{{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}}
input,select{{padding:10px 12px;border:1px solid #ccc;border-radius:10px}}
.controls{{display:grid;grid-template-columns:1fr 180px;gap:10px}}
.meta{{font-size:14px;color:#555}}
.list{{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}}
.card{{background:#fff;border:1px solid #e5e5e5;border-radius:14px;padding:14px}}
.top{{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}}
h1,h3,p{{margin:0}} h3{{font-size:18px}} .idx{{font-size:12px;color:#666}} .memo{{color:#444}}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>약사공론 매물 대시보드</h1>
  <div class="meta">마지막 확인 ID: {state["last_id"]} · 갱신: {html.escape(state["updated_at"] or "-")}</div>
  <div class="controls">
    <input id="q" placeholder="검색어 입력">
    <select id="sort">
      <option value="desc">최신순</option>
      <option value="asc">오래된순</option>
    </select>
  </div>
</header>
<section id="list" class="list">
{''.join(cards) if cards else '<p>현재 수집된 신규 데이터가 없습니다.</p>'}
</section>
</div>
<script>
const q=document.getElementById('q'), sort=document.getElementById('sort'), list=document.getElementById('list');
function apply(){{
  const cards=[...list.querySelectorAll('.card')];
  const term=(q.value||'').toLowerCase().trim();
  cards.forEach(c=>{{
    const hay=(c.dataset.title+' '+c.dataset.tags).toLowerCase();
    c.style.display=!term || hay.includes(term) ? '' : 'none';
  }});
  cards.sort((a,b)=>sort.value==='desc' ? (+b.dataset.idx)-(+a.dataset.idx) : (+a.dataset.idx)-(+b.dataset.idx));
  cards.forEach(c=>list.appendChild(c));
}}
q.addEventListener('input', apply);
sort.addEventListener('change', apply);
</script>
</body>
</html>"""
    DOCS_PATH.write_text(html_doc, encoding="utf-8-sig")

def main():
    state = load_state()
    items, state = scan(state)
    render(items, state)
    save_state(state)

if __name__ == "__main__":
    main()

