import re

src = r"%USERPROFILE%\Downloads\a_feedback.tsv".replace("%USERPROFILE%", __import__("os").environ["USERPROFILE"])
fixed = r"%USERPROFILE%\Downloads\a_feedback_fixed.tsv".replace("%USERPROFILE%", __import__("os").environ["USERPROFILE"])

raw = open(src, "r", encoding="utf-8", errors="ignore").read()
raw = raw.replace("\r","").replace("\\n","\n")   # literal \n -> real newlines

lines = [l for l in raw.splitlines() if l.strip()]

ok=no=0
for l in lines[1:]:
    if re.search(r"(^|\s)OK(\s|$)", l): ok += 1
    elif re.search(r"(^|\s)NO(\s|$)", l): no += 1

open(fixed, "w", encoding="utf-8").write("\n".join(lines) + "\n")

print("FIXED_LINES", max(0,len(lines)-1), "OK", ok, "NO", no, "TOTAL", ok+no)
print("SAVED", fixed)
