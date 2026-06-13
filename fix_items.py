import json
with open('data/items.json', encoding='utf-8') as f:
    c = f.read()
pos = c.rfind('\n  },\n  \"')
fixed = c[:pos + 4] + '\n}'
data = json.loads(fixed)
print(f'복구 항목 수: {len(data)}건')
with open('data/items.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('items.json 복구 완료')
