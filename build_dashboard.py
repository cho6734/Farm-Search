# -*- coding: utf-8 -*-
import json, html, pathlib, requests
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
STATE_PATH = ROOT / "data" / "state.json"
DOCS_PATH = ROOT / "docs" / "index.html"
DETAIL_URL = "https://svc.kpanews.co.kr/jobs/estate/detail?idx={idx}"

def load_state():
    return json.loads(STATE_PATH.read_text(encoding="utf-8-sig"))

def save_state(state):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

def fetch_item(idx):
    url = DETAIL_URL.format(idx=idx)
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None
        data = r.json()
        if not data or not isinstance(data, dict):
            return None
        text = json.dumps(data, ensure_ascii=False)
        if "idx" not in text and "title" not in text and "subject" not in text:
            return None
        return data
    except Exception:
        return None

def pick(item, *keys):
    for k in keys:
        v = item.get(k)
        if v not in (None, "", []):
            return v
    return ""

def norm(item, idx):
    return {
        "idx": idx,
        "title": str(pick(item, "title", "subject", "sj")),
        "region": str(pick(item, "region", "area", "sido", "addr1")),
        "location": str(pick(item, "location", "addr", "address", "addr2")),
        "price": str(pick(item, "price", "deposit", "amount")),
        "rent": str(pick(item, "rent", "monthly_rent", "monthPrice")),
        "phone": str(pick(item, "phone", "tel", "mobile")),
        "memo": str(pick(item, "memo", "content", "desc", "description")),
        "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
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
        cards.append("""
        <article class="card" data-title="{title_attr}" data-tags="{tags_attr}" data-idx="{idx}">
          <div class="top">
            <h3>{title}</h3>
            <span class="idx">#{idx}</span>
          </div>
          <p>{place}</p>
          <p>보증금: {price} / 월세: {rent}</p>
          <p>연락처: {phone}</p>
          <p class="memo">{memo}</p>
        </article>
        """.format(
            title_attr=html.escape(x["title"]),
            tags_attr=html.escape(tags),
            idx=x["idx"],
            title=html.escape(x["title"] or "(제목없음)"),
            place=html.escape(" / ".join([v for v in [x["region"], x["location"]] if v])),
            price=html.escape(x["price"]),
            rent=html.escape(x["rent"]),
            phone=html.escape(x["phone"]),
            memo=html.escape(x["memo"][:200])
        ))

    html_doc = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>약사공론 매물 대시보드</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;margin:0;background:#f7f7f7;color:#111}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
header{display:flex;flex-direction:column;gap:10px;margin-bottom:16px}
input,select{padding:10px 12px;border:1px solid #ccc;border-radius:10px}
.controls{display:grid;grid-template-columns:1fr 180px;gap:10px}
.meta{font-size:14px;color:#555}
.list{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}
.card{background:#fff;border:1px solid #e5e5e5;border-radius:14px;padding:14px}
.top{display:flex;justify-content:space-between;gap:8px;align-items:flex-start}
h1,h3,p{margin:0}
h3{font-size:18px}
.idx{font-size:12px;color:#666}
.memo{color:#444}
</style>
</head>
<body>
<div class="wrap">
<header>
  <h1>약사공론 매물 대시보드</h1>
  <div class="meta">마지막 확인 ID: {last_id} · 갱신: {updated_at}</div>
  <div class="controls">
    <input id="q" placeholder="검색어 입력">
    <select id="sort">
      <option value="desc">최신순</option>
      <option value="asc">오래된순</option>
    </select>
  </div>
</header>
<section id="list" class="list">
{cards_html}
</section>
</div>
<script>
const q = document.getElementById('q');
const sort = document.getElementById('sort');
const list = document.getElementById('list');

function apply() {
  const cards = [...list.querySelectorAll('.card')];
  const term = (q.value || '').toLowerCase().trim();
  cards.forEach(c => {
    const hay = (c.dataset.title + ' ' + c.dataset.tags).toLowerCase();
    c.style.display = (!term || hay.includes(term)) ? '' : 'none';
  });
  cards.sort((a, b) => sort.value === 'desc' ? (+b.dataset.idx) - (+a.dataset.idx) : (+a.dataset.idx) - (+b.dataset.idx));
  cards.forEach(c => list.appendChild(c));
}

q.addEventListener('input', apply);
sort.addEventListener('change', apply);
</script>
</body>
</html>
""".format(
        last_id=state["last_id"],
        updated_at=html.escape(state["updated_at"] or "-"),
        cards_html="".join(cards) if cards else "<p>현재 수집된 신규 데이터가 없습니다.</p>"
    )

    DOCS_PATH.write_text(html_doc, encoding="utf-8")

def main():
    state = load_state()
    items, state = scan(state)
    render(items, state)
    save_state(state)

if __name__ == "__main__":
    main()
