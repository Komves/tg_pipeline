import re
from pathlib import Path
import yaml

SRC_TXT = Path("sources.txt")
CFG_YAML = Path("tg_config.yaml")

def extract_source(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#"):
        return ""

    # @username или t.me ссылка
    m = re.search(r"(@[A-Za-z0-9_]{3,}|https?://t\.me/\S+|t\.me/\S+)", line)
    if not m:
        return ""

    s = m.group(0).strip()

    if s.startswith("t.me/"):
        s = "https://" + s

    # убрать хвосты пунктуации
    s = s.rstrip(").,;\"'")

    return s


def main():
    if not SRC_TXT.exists():
        raise FileNotFoundError("sources.txt not found рядом со скриптом")
    if not CFG_YAML.exists():
        raise FileNotFoundError("tg_config.yaml not found рядом со скриптом")

    cfg = yaml.safe_load(CFG_YAML.read_text(encoding="utf-8"))

    seen = set()
    mix = []
    for raw in SRC_TXT.read_text(encoding="utf-8").splitlines():
        s = extract_source(raw)
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        mix.append(s)

    cfg.setdefault("sources", {})
    cfg["sources"]["MIX"] = mix

    CFG_YAML.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8"
    )

    print("OK: wrote sources.MIX into tg_config.yaml")
    print("MIX count:", len(mix))
    if mix:
        print("First:", mix[0])
        print("Last :", mix[-1])

if __name__ == "__main__":
    main()
