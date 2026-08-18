from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

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

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
    "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
    "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
    "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
    "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
}


CA_CITY_ZIPS = {
    "Los Angeles": "90012",
    "San Francisco": "94102",
    "San Diego": "92101",
    "San Jose": "95113",
    "Sacramento": "95814",
    "Fresno": "93721",
    "Oakland": "94612",
    "Bakersfield": "93301",
    "Anaheim": "92805",
    "Santa Ana": "92701",
    "Riverside": "92501",
    "Stockton": "95202",
    "Irvine": "92614",
    "Long Beach": "90802",
}


def parse_california_name(name_raw: str) -> tuple[str, str]:
    """
    Parses a California CSLB name string into (contractor_name, company_name).
    - 'SMITH, JOHN DAVID' -> contractor_name='John David Smith', company_name='SMITH, JOHN DAVID'
    - 'PACIFIC ROOFING CORP' -> contractor_name='', company_name='PACIFIC ROOFING CORP'
    """
    name_clean = name_raw.strip()
    if not name_clean:
        return "", ""

    words = set(re.findall(r"\b[A-Za-z0-9]+\b", name_clean.upper()))
    is_corporate = bool(words & CORP_INDICATORS)

    if "," in name_clean:
        parts = [p.strip() for p in name_clean.split(",", 1)]
        last_part = parts[0]
        first_part = parts[1] if len(parts) > 1 else ""

        if is_corporate:
            return "", name_clean
        else:
            contractor_name = f"{first_part} {last_part}".strip()
            return contractor_name, name_clean

    if is_corporate:
        return "", name_clean
    else:
        return name_clean, name_clean


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

        if request.individuals_only:
            records = [r for r in records if r.get("contractor_name")]

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_california_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_type_code = CSLB_LICENSE_TYPES.get(request.license_type, CSLB_LICENSE_TYPES["default"])
        city = (request.city or "Los Angeles").title()
        zip_code_param = CA_CITY_ZIPS.get(city, "90012")
        
        records = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**self._browser_launch_options())
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
                
                log(f"Navigating to CSLB Search for {city} (Zip: {zip_code_param})...")
                await page.goto(f"{BASE_URL}/OnlineServices/CheckLicenseII/CheckLicense.aspx", timeout=30000)
                await page.wait_for_selector('a[href*="ZipCodeSearch.aspx"]', timeout=15000)
                await page.click('a[href*="ZipCodeSearch.aspx"]')
                
                await page.wait_for_selector('#txtZipCode', timeout=15000)
                
                log(f"Filling search criteria: ZipCode={zip_code_param}, Type={lic_type_code}...")
                await page.fill('#txtZipCode', zip_code_param)
                await page.select_option('#ddlLicenseType', value=lic_type_code)
                
                log("Submitting CSLB search...")
                await page.click('#MainContent_btnZipCodeSearch')
                await page.wait_for_timeout(4000)
                
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
                                
                            contractor_name, company_name = parse_california_name(name)

                            records.append({
                                "source_url": BASE_URL,
                                "contractor_name": contractor_name,
                                "company_name": company_name,
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
            log(f"California CSLB scrape failed ({type(exc).__name__}): {exc}", "error")
            return []

    @staticmethod
    def _browser_launch_options() -> dict[str, Any]:
        options: dict[str, Any] = {"headless": True}
        if sys.platform != "win32":
            return options

        chrome_paths = (
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        )
        for chrome_path in chrome_paths:
            if chrome_path.is_file():
                options["executable_path"] = str(chrome_path)
                break
        return options
