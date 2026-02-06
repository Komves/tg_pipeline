import sqlite3, os

db = r".\out\index\dedup.sqlite"
out = r".\out\index\canonical_mp4.txt"

con = sqlite3.connect(db)

sql = """
WITH dups AS (SELECT fh, canonical_path FROM dup_groups)
SELECT canonical_path AS path FROM dups
UNION
SELECT f.path
FROM files f
LEFT JOIN dups d ON d.fh = f.fh
WHERE f.status='ok' AND (f.fh IS NULL OR d.fh IS NULL)
"""

rows = con.execute(sql).fetchall()
paths = sorted({r[0] for r in rows})

os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
with open(out, "w", encoding="utf-8") as f:
    for p in paths:
        f.write(p + "\n")

print("canonical_count:", len(paths))
print("written:", os.path.abspath(out))

con.close()
