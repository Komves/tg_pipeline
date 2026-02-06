import asyncio
from pathlib import Path

import yaml
import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError


def read_yaml(path: Path):
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def print_qr_ascii(data: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(data)
    qr.make(fit=True)
    qr.print_ascii(invert=True)


def save_qr_png(data: str, out_path: Path) -> None:
    img = qrcode.make(data)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)


async def main():
    cfg_path = Path("tg_config.yaml")
    if not cfg_path.exists():
        raise FileNotFoundError("tg_config.yaml not found рядом с tg_login_qr.py")

    cfg = read_yaml(cfg_path)
    api_id = int(cfg["telegram"]["api_id"])
    api_hash = str(cfg["telegram"]["api_hash"])

    session_dir = Path(cfg["telegram"].get("session_dir", "data/tg/session"))
    session_name = str(cfg["telegram"].get("session_name", "tg_ingest"))
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = session_dir / session_name

    client = TelegramClient(str(session_path), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        who = getattr(me, "username", None) or getattr(me, "first_name", None) or "unknown"
        print(f"Уже авторизован: {who}")
        await client.disconnect()
        return

    print("\nОткрой Telegram на телефоне:")
    print("Settings -> Devices -> Link Desktop Device / Scan QR\n")
    print("Я покажу QR в консоли и сохраню PNG. После скана может попросить пароль 2FA.\n")

    while True:
        qr = await client.qr_login()
        url = qr.url

        print("\n=== QR (ASCII) ===")
        print_qr_ascii(url)

        out_png = Path("out") / "logs" / "tg_login_qr.png"
        save_qr_png(url, out_png)
        print(f"\nPNG сохранён: {out_png.resolve()}")
        print("Открой PNG и отсканируй телефоном.\n")
        print("Жду сканирования... (если не успеешь — обновлю QR)\n")

        try:
            await qr.wait(timeout=55)
            break
        except asyncio.TimeoutError:
            print("QR истёк или не отсканировали. Обновляю...\n")
            continue
        except SessionPasswordNeededError:
            # После скана Telegram требует пароль 2FA
            print("\nTelegram требует пароль 2FA (Two-Step Verification).")
            pwd = input("Введи пароль 2FA: ")
            await client.sign_in(password=pwd)
            break

    me = await client.get_me()
    who = getattr(me, "username", None) or getattr(me, "first_name", None) or "unknown"
    print(f"\nГотово. Вошли как: {who}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
