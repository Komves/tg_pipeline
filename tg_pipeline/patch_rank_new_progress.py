from pathlib import Path
import re

p = Path(r".\tg_rank_b_clip_list_fast.py")
s = p.read_text(encoding="utf-8")

# 1) Добавим файл прогресса (если еще нет)
if "PROG_PATH" not in s:
    s = s.replace(
        "HB_PATH   = Path",
        "PROG_PATH = Path(r\".\\\\out\\\\logs\\\\rank_new_progress.txt\")\nHB_PATH   = Path"
    )

# 2) Прогресс печатаем чаще (каждые 5, чтобы даже 30 файлов было видно)
s = s.replace("if i % 50 == 0:", "if i % 5 == 0:")

# 3) Инициализация прогресса после todo
pat = r'print\("\[rank-new\] todo:", len\(todo\)\)'
if re.search(pat, s) and "rank_new_progress.txt" in s and "done=0 total" not in s:
    s = re.sub(
        pat,
        'print("[rank-new] todo:", len(todo))\n'
        '    PROG_PATH.parent.mkdir(parents=True, exist_ok=True)\n'
        '    PROG_PATH.write_text(f"done=0 total={len(todo)}\\n", encoding="utf-8")',
        s,
        count=1
    )

# 4) После каждой записи в jsonl: flush + обновление progress-файла
def inject_after(write_snippet: str):
    nonlocal_s = s
    if write_snippet in nonlocal_s and "PROG_PATH.write_text" in nonlocal_s:
        return nonlocal_s
    return nonlocal_s

# Найдём места, где пишем out.write(...) и добавим out.flush()+progress
# (делаем точечно по трем случаям)
s = s.replace(
    'out.write(json.dumps({"path": vp, "status":"fail_frame"}, ensure_ascii=False) + "\\n")',
    'out.write(json.dumps({"path": vp, "status":"fail_frame"}, ensure_ascii=False) + "\\n"); out.flush()\n'
    '                    PROG_PATH.write_text(f"done={i} total={len(todo)}\\ncurrent={vp}\\nstatus=fail_frame\\n", encoding="utf-8")'
)
s = s.replace(
    'out.write(json.dumps({"path": vp, "status":"ok", "score": score}, ensure_ascii=False) + "\\n")',
    'out.write(json.dumps({"path": vp, "status":"ok", "score": score}, ensure_ascii=False) + "\\n"); out.flush()\n'
    '                    PROG_PATH.write_text(f"done={i} total={len(todo)}\\ncurrent={vp}\\nstatus=ok\\n", encoding="utf-8")'
)
s = s.replace(
    'out.write(json.dumps({"path": vp, "status":"fail_embed"}, ensure_ascii=False) + "\\n")',
    'out.write(json.dumps({"path": vp, "status":"fail_embed"}, ensure_ascii=False) + "\\n"); out.flush()\n'
    '                    PROG_PATH.write_text(f"done={i} total={len(todo)}\\ncurrent={vp}\\nstatus=fail_embed\\n", encoding="utf-8")'
)

p.write_text(s, encoding="utf-8")
print("patched:", p)
