from __future__ import annotations

import json
import re
from pathlib import Path

from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest

# Map our internal types to CSLB dropdown values
CSLB_LICENSE_TYPES = {
    "General Contractor": "B",
    "Building Contractor": "B",
    "Residential Contractor": "B",
    "General Engineering": "A",
    "Electrical Contractor": "C10",
    "HVAC Contractor": "C20",
    "Plumbing Contractor": "C36",
    "Roofing Contractor": "C39",
    "default": "B"
}

BASE_URL = "https://www.cslb.ca.gov"
SEARCH_URL = f"{BASE_URL}/OnlineServices/CheckLicenseII/ZipCodeSearch.aspx"


class CaliforniaScraper:
    name = "California CSLB"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Opening California CSLB license search with Playwright...")
        records = await self._try_public_search(request, log)
        if not records:
            log("Live source returned no records or was blocked.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_california_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_type_code = CSLB_LICENSE_TYPES.get(request.license_type, CSLB_LICENSE_TYPES["default"])
        city = (request.city or "Los Angeles").title()
        
        records = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = await context.new_page()
                # Simple stealth to bypass basic headless checks
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                log(f"Navigating to CSLB Search for {city}...")
                # Must visit base page first and click the link to establish session cookies/referer
                await page.goto(f"{BASE_URL}/OnlineServices/CheckLicenseII/CheckLicense.aspx", timeout=30000)
                await page.wait_for_selector('a[href="ZipCodeSearch.aspx"]', timeout=15000)
                await page.click('a[href="ZipCodeSearch.aspx"]')
                
                # Wait for form to load
                await page.wait_for_selector('#txtCity', timeout=15000)
                
                # Fill out the form
                log(f"Filling out search criteria: City={city}, Type={lic_type_code}...")
                await page.fill('#txtCity', city)
                await page.select_option('#ddlLicenseType', value=lic_type_code)
                
                # Click Search
                log("Submitting search...")
                await page.click('#MainContent_btnZipCodeSearch')
                
                # Wait for results table or no results message
                try:
                    await page.wait_for_selector('table, span:has-text("No records")', timeout=20000)
                except Exception:
                    log("Timeout waiting for results to load.", "error")
                    await browser.close()
                    return []
                    
                content = await page.content()
                soup = BeautifulSoup(content, 'html.parser')
                
                if "No records" in content or "no results" in content.lower():
                    log("No records found for this city and classification.")
                    await browser.close()
                    return []
                    
                # Parse the results table
                # Expected columns: Export, License #, Name, Address, City, Zip, Phone #
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                        # Skip header and empty rows
                        if not cells or "License #" in cells:
                            continue
                            
                        # CSLB search results usually have 7 columns
                        if len(cells) >= 6:
                            # Typical layout: [Checkbox], License #, Name, Address, City, Zip, Phone #
                            lic_num = cells[1]
                            name = cells[2]
                            address = cells[3]
                            row_city = cells[4]
                            zip_code = cells[5]
                            phone = cells[6] if len(cells) > 6 else ""
                            
                            if not lic_num or not name:
                                continue
                                
                            # CSLB search page only shows active matching records by default
                            records.append({
                                "source_url": BASE_URL,
                                "contractor_name": name, # Map Business Name here so it's not blank in the CSV export
                                "company_name": "",
                                "license_number": lic_num,
                                "license_type": request.license_type,
                                "license_status": "Active",
                                "address": address,
                                "city": row_city,
                                "state": "CA",
                                "zip_code": zip_code,
                                "phone": phone
                            })
                            
                log(f"Extracted {len(records)} records from page.")
                await browser.close()
                return records
                
        except Exception as exc:
            log(f"California CSLB scrape failed: {exc}", "error")
            return []
