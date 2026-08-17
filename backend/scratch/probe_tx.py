"""
Check TDLR license types dropdown + test TECL electrical contractor search
"""
import asyncio
import httpx
from bs4 import BeautifulSoup
from urllib.parse import urljoin
import sys

BASE = "https://www.tdlr.texas.gov/LicenseSearch/"

async def main():
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=20) as c:
        # 1. Get the form page to see all license type dropdown options
        r = await c.get(BASE + "LicenseSearch.asp")
        soup = BeautifulSoup(r.content, "html.parser")
        for sel in soup.find_all("select"):
            name = sel.get("name", "?")
            opts = [(o.get("value",""), o.get_text(strip=True)) for o in sel.find_all("option")]
            print(f"\nSELECT [{name}] - {len(opts)} options:")
            for v, t in opts[:40]:
                sys.stdout.buffer.write(f"  {v!r} -> {t!r}\n".encode("utf-8","replace"))

        # 2. Submit a search filtering to TECL (Electrical Contractors - company license)
        print("\n\n=== TECL ELECTRICAL CONTRACTOR SEARCH ===")
        form = soup.find("form")
        action = form.get("action","") if form else ""
        full_url = urljoin(BASE, action)

        payload = {
            "pht_lic": "",
            "pht_expdt": "",
            "pht_oth_name": "",
            "phy_zip": "",
            "B1": "Search",
            "B2": "Reset",
            "tdlr_status": "ACT",
            "phy_city": "HOUSTON",
            "phy_cnty": "-1",
        }

        # Also try with license type select
        # Check if there's a license type select field name
        for inp in soup.find_all(["input","select"]):
            if inp.name == "select":
                sys.stdout.buffer.write(f"select field: {inp.get('name')!r}\n".encode("utf-8","replace"))
            elif inp.get("name") and "lic" in inp.get("name","").lower():
                sys.stdout.buffer.write(f"input: {inp.get('name')!r} = {inp.get('value','')!r}\n".encode("utf-8","replace"))

        r2 = await c.post(full_url, data=payload)
        soup2 = BeautifulSoup(r2.content, "html.parser")

        # Get detail links
        links = [urljoin(BASE, a["href"]) for a in soup2.find_all("a", href=True) if "SearchResultDetail" in a["href"]]
        print(f"Detail links found: {len(links)}")

        # Get first 3 data rows
        for t in soup2.find_all("table"):
            rows = t.find_all("tr")
            header_found = False
            for row in rows:
                cells = [c.get_text(strip=True)[:50] for c in row.find_all(["td","th"])]
                if not cells:
                    continue
                if "License#" in cells[0] or "License#" in str(cells):
                    header_found = True
                    continue
                if header_found and len(cells) >= 3 and cells[0]:
                    sys.stdout.buffer.write(f"  {cells[:6]}\n".encode("utf-8","replace"))

        # Fetch first detail page
        if links:
            print(f"\n=== DETAIL PAGE: {links[0]} ===")
            r3 = await c.get(links[0])
            soup3 = BeautifulSoup(r3.content, "html.parser")
            for t in soup3.find_all("table"):
                for row in t.find_all("tr"):
                    cells = [c.get_text(strip=True).replace("\xa0"," ")[:80] for c in row.find_all(["td","th"])]
                    if cells and any(x for x in cells):
                        sys.stdout.buffer.write(f"  {cells}\n".encode("utf-8","replace"))

asyncio.run(main())
