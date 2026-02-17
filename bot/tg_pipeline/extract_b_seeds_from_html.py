import re, os, html, urllib.parse

inp = r"C:\Users\Марк\Downloads\tg_video_audit\view_top.html"
out = r".\out\index\b_seeds.txt"

s = open(inp, "r", encoding="utf-8", errors="ignore").read()
s = html.unescape(s)

# href="file:///C:/.../something.mp4"
urls = re.findall(r'href="(file:///[^"]+\.(?:mp4|mkv|mov))"', s, flags=re.I)

paths = []
for u in urls:
    u = urllib.parse.unquote(u)  # decode %20 etc
    # file:///C:/Users/... -> C:\Users\...
    if u.lower().startswith("file:///"):
        p = u[8:]  # strip 'file:///'
        p = p.replace("/", "\\")
        paths.append(p)

# unique + exists
uniq = []
seen = set()
for p in paths:
    if p in seen:
        continue
    seen.add(p)
    uniq.append(p)

exists = [p for p in uniq if os.path.exists(p)]

os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    for p in exists:
        f.write(p + "\n")

print("links_in_html:", len(urls))
print("unique_paths:", len(uniq))
print("exists_on_disk:", len(exists))
print("written:", os.path.abspath(out))
print("first5:")
for p in exists[:5]:
    print(p)
