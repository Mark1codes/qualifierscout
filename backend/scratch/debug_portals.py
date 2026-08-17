"""Debug: Check FL license types for Board 06 and TX contractor license detail"""
import asyncio
import httpx
from bs4 import BeautifulSoup


async def check_fl_license_types():
    print("\n=== FLORIDA BOARD 06 LICENSE TYPES ===")
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
        r = await client.get("https://www.myfloridalicense.com/wl11.asp?mode=0&SID=")
        soup = BeautifulSoup(r.content, "html.parser")
        form = soup.find("form")
        hidden = {tag.get("name"): tag.get("value", "") for tag in form.find_all("input", type="hidden")}
        hidden["SearchType"] = "City"
        r2 = await client.post("https://www.myfloridalicense.com/wl11.asp?mode=1&SID=&brd=&typ=", data=hidden)
        soup2 = BeautifulSoup(r2.content, "html.parser")
        
        # Now select Board 06 and get license types
        hidden2 = {tag.get("name"): tag.get("value", "") for tag in soup2.find_all("input", type="hidden")}
        hidden2["Board"] = "06"
        hidden2["hBoard"] = "06"
        hidden2["LicenseType"] = ""
        hidden2["City"] = "MIAMI"
        hidden2["County"] = ""
        hidden2["State"] = "FL"
        hidden2["RecsPerPage"] = "10"
        
        # Try fetching with blank LicenseType to see what comes back
        r3 = await client.post(
            "https://www.myfloridalicense.com/wl11.asp?mode=2&search=City&SID=&brd=06&typ=",
            data=hidden2
        )
        soup3 = BeautifulSoup(r3.content, "html.parser")
        
        # Check for any select dropdowns now
        for sel in soup3.find_all("select"):
            name = sel.get("name", "?")
            opts = [(o.get("value", ""), o.get_text(strip=True)) for o in sel.find_all("option")]
            print(f"SELECT [{name}]: {opts[:10]}")
        
        # Check result rows
        tables = soup3.find_all("table")
        print(f"\nTables found: {len(tables)}")
        for t in tables[:2]:
            rows = t.find_all("tr")[:5]
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if cells:
                    print(f"  Row: {cells}")


async def check_tx_contractor_detail():
    print("\n=== TEXAS CONTRACTOR LICENSE DETAIL ===")
    # Search for HVAC contractors (TACL) which are business licenses
    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
        base = "https://www.tdlr.texas.gov/LicenseSearch/"
        r = await client.get(base)
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.find("form")
        action = form.get("action", "") if form else ""
        from urllib.parse import urljoin
        full_url = urljoin(base, action)
        
        # Search for HVAC contractor license (TACL) in Austin
        payload = {
            "pht_lic": "TACL",  # HVAC contractor prefix
            "pht_expdt": "",
            "pht_oth_name": "",
            "phy_zip": "",
            "B1": "Search",
            "B2": "Reset",
            "tdlr_status": "ACT",
            "phy_city": "AUSTIN",
            "phy_cnty": "-1"
        }
        r2 = await client.post(full_url, data=payload)
        soup2 = BeautifulSoup(r2.content, "html.parser")
        
        # Get first detail URL
        first_url = None
        for a in soup2.find_all("a", href=True):
            if "SearchResultDetail" in a["href"]:
                first_url = urljoin(base, a["href"])
                break
        
        print(f"First TACL detail URL: {first_url}")
        if first_url:
            r3 = await client.get(first_url)
            soup3 = BeautifulSoup(r3.content, "html.parser")
            for t in soup3.find_all("table"):
                for row in t.find_all("tr"):
                    cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                    if cells:
                        print(f"  {cells}")


async def main():
    await check_fl_license_types()
    await check_tx_contractor_detail()


asyncio.run(main())
