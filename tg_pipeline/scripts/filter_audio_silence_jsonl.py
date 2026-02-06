import json, os, subprocess, re
from pathlib import Path

RAW_ROOT = Path("data/tg/raw/MIX")

REQUIRE_AUDIO = os.environ.get("REQUIRE_AUDIO", "1") == "1"
CHECK_SEC = int(os.environ.get("SILENCE_SEC", "15"))
NOISE_DB = os.environ.get("SILENCE_DB", "-35")
MIN_SILENCE_D = os.environ.get("SILENCE_DUR", "0.4")
MAX_SILENCE_PCT = float(os.environ.get("SILENCE_PCT", "70"))

def resolve_path(p: str) -> Path:
    pp = Path(p.replace("\\\\", "\\"))
    if pp.exists():
        return pp
    p2 = (Path(".") / pp).resolve()
    if p2.exists():
        return p2
    # fallback: search by basename
    hits = list(RAW_ROOT.rglob(pp.name))
    if hits:
        return hits[0]
    return p2

def has_audio(fp: Path) -> bool:
    cmd = [
        "ffprobe","-v","error",
        "-select_streams","a:0",
        "-show_entries","stream=codec_type",
        "-of","csv=p=0",
        str(fp)
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return "audio" in (r.stdout or "").strip().lower()

def silence_pct(fp: Path) -> float:
    # sum silence_duration from silencedetect in first CHECK_SEC seconds
    cmd = [
        "ffmpeg","-hide_banner","-i",str(fp),
        "-t",str(CHECK_SEC),
        "-af",f"silencedetect=noise={NOISE_DB}dB:d={MIN_SILENCE_D}",
        "-f","null","-"
    ]
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    txt = (r.stderr or "")
    durs = [float(x) for x in re.findall(r"silence_duration:\s*([0-9.]+)", txt)]
    total = sum(durs)
    return 100.0 * total / float(CHECK_SEC)

def main():
    import sys
    if len(sys.argv) != 3:
        print("Usage: python scripts/filter_audio_silence_jsonl.py <in.jsonl> <out.jsonl>")
        raise SystemExit(2)

    inp = Path(sys.argv[1])
    outp = Path(sys.argv[2])
    rows = []
    for line in inp.read_text(encoding="utf-8", errors="ignore").splitlines():
        line=line.strip()
        if not line: 
            continue
        try:
            rows.append(json.loads(line))
        except:
            pass

    kept = 0
    outp.parent.mkdir(parents=True, exist_ok=True)
    with outp.open("w", encoding="utf-8") as w:
        for r in rows:
            p = r.get("path")
            if not isinstance(p,str) or not p:
                continue
            fp = resolve_path(p)
            if not fp.exists():
                continue

            if REQUIRE_AUDIO and not has_audio(fp):
                continue

            sp = silence_pct(fp)
            if sp >= MAX_SILENCE_PCT:
                continue

            r["__silence_pct"] = sp
            w.write(json.dumps(r, ensure_ascii=False) + "\n")
            kept += 1

    print(f"[FILTER] in={len(rows)} kept={kept} out={outp}")

if __name__ == "__main__":
    main()
