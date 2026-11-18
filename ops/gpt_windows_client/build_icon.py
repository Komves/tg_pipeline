"""Create a Windows-compatible (DIB/BMP-backed) multi-size GPT.ico.

The former ICO embedded a palette PNG frame and Windows Explorer rendered the
shortcut as a blank document on some machines. Build a 32-bit Windows icon
from the approved artwork; do not copy the old ICO into the new installer.
"""
from pathlib import Path
from PIL import Image, IcoImagePlugin

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "GPT_source.png"
OUTPUT = ROOT / "GPT.ico"
SIZES = {(16, 16), (24, 24), (32, 32), (48, 48), (64, 64)}

with Image.open(SOURCE) as original:
    image = original.convert("RGBA")
    image.save(OUTPUT, format="ICO", bitmap_format="bmp", sizes=sorted(SIZES))

with Image.open(OUTPUT) as result:
    if result.format != "ICO" or result.info.get("sizes") != SIZES:
        raise SystemExit("Generated ICO has missing or invalid image sizes")

raw = OUTPUT.read_bytes()
if raw[:4] != b"\x00\x00\x01\x00":
    raise SystemExit("Not a Windows .ico file")
# ICO header is 6 bytes, first entry is 16 bytes, first bitmap starts at
# the offset stored in that entry. A DIB begins with a 40-byte BITMAPINFOHEADER.
offset = int.from_bytes(raw[18:22], "little")
if raw[offset:offset+4] != b"\x28\x00\x00\x00":
    raise SystemExit("Expected Windows bitmap icon, found PNG or invalid data")
print(f"Created {OUTPUT.name}: {len(raw)} bytes; sizes: {sorted(SIZES)}")
