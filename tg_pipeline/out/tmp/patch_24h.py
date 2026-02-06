import re, os
from pathlib import Path

root = r"C:\Users\Марк\tg_pipeline\tg_pipeline"
engine = Path(root) / "scripts" / "a_engine.py"
s = engine.read_text(encoding="utf-8")

# 1) ensure datetime import
if "from datetime import datetime, timedelta" not in s:
    s = s.replace(
        "import os, re, json, glob, shutil, random, subprocess, hashlib, argparse",
        "import os, re, json, glob, shutil, random, subprocess, hashlib, argparse\nfrom datetime import datetime, timedelta"
    )

# 2) insert cutoff after wl_p line (inside build_next_batch)
if re.search(r"cutoff\s*=\s*datetime\.now\(\)\s*-\s*timedelta", s) is None:
    s = re.sub(
        r'(wl_p\s*=\s*os\.path\.join\(root,\s*"out","config","a_channels_whitelist\.txt"\)\s*\n)',
        r'\1\n    cutoff = datetime.now() - timedelta(hours=24)\n',
        s
    )

# 3) add mtime filter right after p=normpath(os.path.join(r,fn))
if "datetime.fromtimestamp(os.path.getmtime(p)) < cutoff" not in s:
    s = re.sub(
        r'(p\s*=\s*normpath\(os\.path\.join\(r,fn\)\)\s*\n)',
        r'\1                    try:\n                        if datetime.fromtimestamp(os.path.getmtime(p)) < cutoff:\n                            continue\n                    except:\n                        continue\n',
        s
    )

engine.write_text(s, encoding="utf-8")
print("PATCHED_24H_OK")
