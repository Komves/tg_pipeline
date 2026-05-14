from seleniumbase import SB
import re
from urllib.parse import quote

query = "шпаклевка"
url = "https://www.vseinstrumenti.ru/search/?what=" + quote(query)

with SB(uc=True, headless=False) as sb:
    sb.open(url)
    sb.sleep(10)

    html = sb.get_page_source()
    prices = re.findall(r"\d[\d\s]{0,10}\s*₽", html)

    print("TITLE =", sb.get_title())
    print("URL =", sb.get_current_url())
    print("PRICES COUNT =", len(prices))
    print("PRICES SAMPLE =", prices[:20])