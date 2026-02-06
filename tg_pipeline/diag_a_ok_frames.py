import json, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG  = json.loads((ROOT/"out/config/engine.local.json").read_text(encoding="utf-8-sig"))

FEEDBACK = ROOT/"out/logs/a_feedback_1.tsv"
OUT_TSV  = ROOT/"out/logs/a_diag_ok_frames.tsv"

SEEK_TIMES = [0.0, 0.1, 0.3, 0.5, 1.0, 1.5, 2.5]
FFMPEG_TIMEOUT = 20

def read_ok(tsv: Path):
    lines = tsv.read_text(encoding="utf-8", errors="ignore").splitlines()
    out=[]
    for l in lines[1:]:
        parts = l.split("\t")
        if len(parts) >= 4 and parts[1].strip().upper() == "OK":
            out.append(parts[3].strip())
    # unique keep order
    seen=set()
    uniq=[]
    for p in out:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq

def ffprobe(p: str):
    try:
        r = subprocess.run(
            ["ffprobe","-v","error",
             "-show_entries","format=duration:stream=codec_name,codec_type,width,height",
             "-of","json", p],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return None, (r.stderr or "").strip()[-200:]
        return json.loads(r.stdout), ""
    except Exception as e:
        return None, str(e)[:200]

def try_frame(p: str, t: float, outjpg: Path):
    # Try fast extract
    try:
        r = subprocess.run(
            ["ffmpeg","-hide_banner","-loglevel","error",
             "-threads","1","-an","-sn",
             "-ss", str(max(0.0,t)),
             "-i", p,
             "-frames:v","1","-q:v","2", str(outjpg)],
            capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
        )
        ok = (r.returncode == 0 and outjpg.exists() and outjpg.stat().st_size > 0)
        err = (r.stderr or "").strip()
        return ok, err[-200:]
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, f"EXC:{e}"

def main():
    if not FEEDBACK.exists():
        raise SystemExit("missing a_feedback_1.tsv")

    ok_paths = read_ok(FEEDBACK)
    if not ok_paths:
        raise SystemExit("no OK rows found")

    tmp = Path(tempfile.mkdtemp(prefix="adiag_"))
    lines = ["path\texists\tduration\tvcodec\tw\th\tframe_ok\tseek_used\terr"]

    for p in ok_paths:
        src = Path(p)
        exists = src.exists()
        dur = ""
        vcodec = ""
        w = ""
        h = ""
        ferr = ""
        if exists:
            meta, perr = ffprobe(str(src))
            ferr = perr
            if meta:
                try:
                    dur = str(round(float(meta["format"]["duration"]), 3))
                except: pass
                try:
                    for s in meta.get("streams", []):
                        if s.get("codec_type") == "video":
                            vcodec = str(s.get("codec_name",""))
                            w = str(s.get("width",""))
                            h = str(s.get("height",""))
                            break
                except: pass

        frame_ok = "0"
        seek_used = ""
        err = ferr

        if exists:
            for t in SEEK_TIMES:
                outjpg = tmp / f"f_{abs(hash(p))}_{int(t*10)}.jpg"
                ok, e = try_frame(str(src), t, outjpg)
                if ok:
                    frame_ok = "1"
                    seek_used = str(t)
                    err = ""
                    break
                else:
                    err = e

        lines.append(f"{p}\t{int(exists)}\t{dur}\t{vcodec}\t{w}\t{h}\t{frame_ok}\t{seek_used}\t{err}")

    OUT_TSV.parent.mkdir(parents=True, exist_ok=True)
    OUT_TSV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("OK. wrote:", OUT_TSV)
    print("OK rows:", len(ok_paths))

if __name__ == "__main__":
    main()
