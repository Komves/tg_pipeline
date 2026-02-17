import re, os

root = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
py = os.path.join(root, "scripts", "a_engine.py")

s = open(py, "r", encoding="utf-8").read()

# 1) add md5 seen file path load after EXC line
if "a_seen_md5.txt" not in s:
    s = s.replace(
        "EXC  = [w.lower() for w in load_lines(exc_p)]\n",
        "EXC  = [w.lower() for w in load_lines(exc_p)]\n"
        "md5_p = os.path.join(root, 'out','logs','a_seen_md5.txt')\n"
        "seen_md5 = set(load_lines(md5_p))\n"
    )

# 2) in add_from: skip if md5 already seen globally
s = s.replace(
    "if not h or h in used:\n                continue\n",
    "if (not h) or (h in used) or (h in seen_md5):\n                continue\n"
)

# 3) after pick finalized, persist new md5s
marker = "    # if still empty -> return 0 safely\n"
if marker in s and "write_text(md5_p" not in s:
    s = s.replace(
        marker,
        "    # persist seen md5 across batches\n"
        "    if pick:\n"
        "        os.makedirs(os.path.join(root,'out','logs'), exist_ok=True)\n"
        "        for h in used:\n"
        "            seen_md5.add(h)\n"
        "        write_text(md5_p, '\\n'.join(sorted(seen_md5))+'\\n')\n\n"
        + marker
    )

open(py, "w", encoding="utf-8").write(s)
print("PATCHED_OK")
