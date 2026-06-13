with open('build_dashboard.py', encoding='utf-8') as f:
    src = f.read()

old_save = '''def save_items(items):
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    ITEMS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")'''
new_save = '''def save_items(items):
    # 원자적 쓰기: 임시 파일에 저장 후 교체 (쓰기 도중 잘림 방지)
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ITEMS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(ITEMS_PATH)'''

if old_save in src:
    src = src.replace(old_save, new_save)
    print('save_items() 수정 완료')
else:
    print('경고: 패턴 불일치')

with open('build_dashboard.py', 'w', encoding='utf-8') as f:
    f.write(src)
print('저장 완료')
