with open('crawler_pharmall.py', encoding='utf-8') as f:
    src = f.read()

old_pt = '    property_type_raw = trade.get("property_type") or ""'
new_pt = '    # property_type: trade -> building -> 최상위 순서로 탐색\n    property_type_raw = trade.get("property_type") or building.get("property_type") or d.get("property_type") or ""'

old_ad = '    approval_date     = sanitize_text(trade.get("approval_date") or "", max_len=30)'
new_ad = '    # approval_date: trade -> building -> 최상위 순서로 탐색\n    approval_date     = sanitize_text(trade.get("approval_date") or building.get("approval_date") or d.get("approval_date") or "", max_len=30)'

changed = 0
for old, new, name in [(old_pt, new_pt, 'property_type'), (old_ad, new_ad, 'approval_date')]:
    if old in src:
        src = src.replace(old, new)
        changed += 1
        print(f'{name} 수정 완료')
    else:
        print(f'경고: {name} 패턴 불일치')

with open('crawler_pharmall.py', 'w', encoding='utf-8') as f:
    f.write(src)
print(f'저장 완료 ({changed}개 수정)')
