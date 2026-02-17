import sys, datetime

label = sys.argv[1]
path  = sys.argv[2]

ts = datetime.datetime.utcnow().isoformat()

with open(r"..\out\logs\a_feedback_master.tsv","a",encoding="utf-8") as f:
    f.write(f"{ts}\t{label}\t0\t{path}\n")
