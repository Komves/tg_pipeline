import json
import requests

VIN = "Z8NTANT32ES114310"

API_KEY = "1b198db1afmsh4e0354c1d739395p1101bbjsn72b5e831a968'"

url = "https://vin-decoder-api-europe.p.rapidapi.com/vin_decoder"

headers = {
    "x-rapidapi-key": API_KEY,
    "x-rapidapi-host": "vin-decoder-api-europe.p.rapidapi.com",
}

params = {
    "vin": VIN
}

session = requests.Session()
session.trust_env = False  # не брать proxy из Windows/окружения

r = session.get(
    url,
    headers=headers,
    params=params,
    timeout=30,
)

print("STATUS:", r.status_code)
print()

try:
    data = r.json()
except Exception:
    print(r.text)
    raise SystemExit

print(json.dumps(data, ensure_ascii=False, indent=2))