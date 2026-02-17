import sqlite3, os
from pathlib import Path

db = Path(r".\out\index\dedup_b_candidates.sqlite")
out = Path(r".\out\index\b_candidates_canonical.txt")

con = sqlite3.connect(db)

rows = con.execute("""
WITH dups AS (
    SELECT fh, canonical_path FROM dup_groups
)
SELECT canonical_path AS path FROM dups
UNION
SELECT f.path
FROM files f
LEFT JOIN dups d ON d.fh = f.fh
WHERE f.status = 'ok' AND d.fh IS NULL
""").fetchall()

paths = sorted({r[0] for r in rows})

out.parent.mkdir(parents=True, exist_ok=True)
out.write_text("\n".join(paths) + ("\n" if paths else ""), encoding="utf-8")

print("canonical_count:", len(paths))
print("written:", out.resolve())

con.close()
