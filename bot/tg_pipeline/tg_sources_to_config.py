import re
from pathlib import Path
import yaml

SRC_TXT = Path("sources.txt")
CFG_YAML = Path("tg_config.yaml")

DEFAULT_CATEGORY = "A"  # поменяй на "B", если хочешь всё сразу в B


def extract_source(line: str) -> str:
    line = line.strip()
    if not line or line.startswith("#"):
        return ""

    # найдём @username или t.me ссылку внутри строки
    m = re.search(r"(@[A-Za-z0-9_]{3,}|https?://t\.me/\S+|t\.me/\S+)", line)
    if not m:
        return ""

    s = m.group(0).strip()

    # нормализуем t.me/xxx -> https://t.me/xxx
    if s.startswith("t.me/"):
        s = "https://" + s

    # уберём хвосты типа ')' ',' '.' если прилепились
    s = s.rstrip(").,;\"'")

    return s


def main():
    if not SRC_TXT.exists():
        raise FileNotFoundError("sources.txt not found рядом со скриптом")

    if not CFG_YAML.exists():
        raise FileNotFoundError("tg_config.yaml not found рядом со скриптом")

    cfg = yaml.safe_load(CFG_YAML.read_text(encoding="utf-8"))
    cfg.setdefault("sources", {})
    cfg["sources"].setdefault("A", [])
    cfg["sources"].setdefault("B", [])

    seen = set()
    collected = []

    for raw in SRC_TXT.read_text(encoding="utf-8").splitlines():
        s = extract_source(raw)
        if not s:
            continue
        k = s.lower()
        if k in seen:
            continue
        seen.add(k)
        collected.append(s)

    cfg["sources"][DEFAULT_CATEGORY] = collected

    CFG_YAML.write_text(
        yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True),
        encoding="utf-8"
    )

    print("OK: tg_config.yaml updated")
    print(f"Loaded: {len(collected)} sources into category {DEFAULT_CATEGORY}")
    if len(collected) > 0:
        print("Example:", collected[0])


if __name__ == "__main__":
    main()
