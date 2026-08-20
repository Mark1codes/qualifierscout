from __future__ import annotations

import asyncio
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
        """Direct HTTP search against California CSLB ASP.NET portal (Fast, Free, 0 Browser Overheads)."""
        lic_type_code = CSLB_LICENSE_TYPES.get(request.license_type, CSLB_LICENSE_TYPES["default"])
        city = (request.city or "Los Angeles").title()
        zip_code_param = CA_CITY_ZIPS.get(city, "90012")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": f"{BASE_URL}/OnlineServices/CheckLicenseII/CheckLicense.aspx"
        }

        try:
            import httpx
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                log(f"Querying California CSLB directly for {request.license_type} in {city} (Zip: {zip_code_param})...")
                
                # Step 1: Initial GET to fetch ASP.NET __VIEWSTATE
                r1 = await client.get(SEARCH_URL, headers=headers)
                if r1.status_code != 200:
                    log(f"California CSLB GET status {r1.status_code}, trying ZenRows...", "warning")
                    return await self._try_zenrows_search(request, log)

                soup1 = BeautifulSoup(r1.text, "html.parser")
                viewstate = soup1.find("input", id="__VIEWSTATE")
                viewstate_gen = soup1.find("input", id="__VIEWSTATEGENERATOR")
                event_val = soup1.find("input", id="__EVENTVALIDATION")

                if not viewstate:
                    log("California CSLB __VIEWSTATE element missing", "warning")
                    return await self._try_zenrows_search(request, log)

                data = {
                    "__VIEWSTATE": viewstate.get("value", ""),
                    "__VIEWSTATEGENERATOR": viewstate_gen.get("value", "") if viewstate_gen else "",
                    "__EVENTVALIDATION": event_val.get("value", "") if event_val else "",
                    "ctl00$MainContent$txtZipCode": zip_code_param,
                    "ctl00$MainContent$ddlLicenseType": lic_type_code,
                    "ctl00$MainContent$btnZipCodeSearch": "Search"
                }

                # Step 2: POST form payload
                post_headers = dict(headers)
                post_headers["Content-Type"] = "application/x-www-form-urlencoded"
                r2 = await client.post(SEARCH_URL, headers=post_headers, data=data)
                if r2.status_code != 200:
                    log(f"California CSLB POST status {r2.status_code}, trying ZenRows...", "warning")
                    return await self._try_zenrows_search(request, log)

                soup2 = BeautifulSoup(r2.text, "html.parser")
                raw_items = []
                tables = soup2.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                        if not cells or "License #" in cells:
                            continue
                        if len(cells) >= 6:
                            lic_num = cells[1]
                            name = cells[2]
                            address = cells[3]
                            row_city = cells[4]
                            zip_code = cells[5]
                            phone = cells[6] if len(cells) > 6 else ""

                            if not lic_num or not name:
                                continue

                            raw_items.append({
                                "lic_num": lic_num,
                                "name": name,
                                "address": address,
                                "city": row_city,
                                "zip_code": zip_code,
                                "phone": phone
                            })

                if not raw_items:
                    log("California CSLB search returned 0 records.", "warning")
                    return await self._try_zenrows_search(request, log)

                log(f"Extracted {len(raw_items)} initial search records. Resolving individual contractor personnel names...")

                # Parallel resolution helper for individual personnel names
                async def resolve_personnel(item):
                    contractor_name, company_name = parse_california_name(item["name"])
                    if not contractor_name:
                        person_name = await self._fetch_personnel_name(client, item["lic_num"], headers)
                        if person_name:
                            contractor_name = person_name

                    return {
                        "source_url": BASE_URL,
                        "contractor_name": contractor_name,
                        "company_name": company_name,
                        "license_number": item["lic_num"],
                        "license_type": request.license_type,
                        "license_status": "Active",
                        "address": item["address"],
                        "city": item["city"],
                        "state": "CA",
                        "zip_code": item["zip_code"],
                        "phone": item["phone"]
                    }

                # Resolve up to request.max_records personnel names in parallel
                records = await asyncio.gather(*[resolve_personnel(item) for item in raw_items[: request.max_records]])
                log(f"Successfully retrieved {len(records)} California CSLB records with individual contractor names.")
                return records

        except Exception as exc:
            log(f"California CSLB direct search failed: {exc}, attempting ZenRows fallback...", "warning")
            return await self._try_zenrows_search(request, log)

    async def _fetch_personnel_name(self, client, lic_num: str, headers: dict) -> str:
        """Fetches individual RMO / Qualifier / Owner / Officer name from CSLB license detail pages."""
        try:
            detail_url = f"{BASE_URL}/OnlineServices/CheckLicenseII/LicenseDetail.aspx?LicNum={lic_num}"
            r1 = await client.get(detail_url, headers=headers)
            if r1.status_code != 200:
                return ""

            # Quick regex match for qualifying individual in detail text
            qualifier_match = re.search(r'qualifying individual\s+([A-Z\s,]+?)\s+certified', r1.text, re.IGNORECASE)
            if qualifier_match:
                name_str = qualifier_match.group(1).strip()
                if name_str and len(name_str.split()) >= 2:
                    return self._clean_person_name(name_str)

            # POST to PersonnelLink if present
            soup1 = BeautifulSoup(r1.text, "html.parser")
            viewstate = soup1.find("input", id="__VIEWSTATE")
            event_val = soup1.find("input", id="__EVENTVALIDATION")

            if viewstate:
                post_data = {
                    "__VIEWSTATE": viewstate.get("value", ""),
                    "__EVENTVALIDATION": event_val.get("value", "") if event_val else "",
                    "ctl00$MainContent$PersonnelLink.x": "10",
                    "ctl00$MainContent$PersonnelLink.y": "10"
                }
                post_headers = dict(headers)
                post_headers["Content-Type"] = "application/x-www-form-urlencoded"
                r2 = await client.post(detail_url, headers=post_headers, data=post_data)
                
                soup2 = BeautifulSoup(r2.text, "html.parser")
                for tr in soup2.find_all("tr"):
                    row_text = tr.get_text()
                    if any(role in row_text for role in ["RMO", "QUALIFIER", "SOLE OWNER", "CEO", "PRES"]):
                        for td in tr.find_all("td"):
                            td_text = td.get_text(strip=True)
                            if td_text.startswith("Name"):
                                name_val = td_text.replace("Name", "").split("Title")[0].strip()
                                if name_val and len(name_val.split()) >= 2:
                                    return self._clean_person_name(name_val)
            return ""
        except Exception:
            return ""

    @staticmethod
    def _clean_person_name(raw_name: str) -> str:
        """Formats 'SMITH, JOHN DAVID' or 'JOHN DAVID SMITH' into 'John David Smith'."""
        raw_clean = raw_name.strip()
        if not raw_clean:
            return ""
        if "," in raw_clean:
            parts = [p.strip() for p in raw_clean.split(",", 1)]
            return f"{parts[1].title()} {parts[0].title()}".strip()
        return raw_clean.title()

    async def _try_zenrows_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Preserved ZenRows fallback strategy for California CSLB for future subscription use."""
        try:
            import os
            import httpx
            zenrows_api_key = os.getenv("ZENROWS_API_KEY", "").strip()
            if not zenrows_api_key:
                log("ZenRows API key not configured for California fallback.", "info")
                return []

            lic_type_code = CSLB_LICENSE_TYPES.get(request.license_type, CSLB_LICENSE_TYPES["default"])
            city = (request.city or "Los Angeles").title()
            zip_code_param = CA_CITY_ZIPS.get(city, "90012")

            zr_url = "https://api.zenrows.com/v1/"
            target_url = f"{BASE_URL}/OnlineServices/CheckLicenseII/ZipCodeSearch.aspx"
            
            params = {
                "apikey": zenrows_api_key,
                "url": target_url,
                "js_render": "true",
                "premium_proxy": "true",
                "proxy_country": "us"
            }

            async with httpx.AsyncClient(timeout=90) as client:
                log("Executing California CSLB ZenRows fallback...")
                resp = await client.get(zr_url, params=params)
                if resp.status_code == 200:
                    soup = BeautifulSoup(resp.text, 'html.parser')
                    records = []
                    for table in soup.find_all("table"):
                        for row in table.find_all("tr"):
                            cells = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
                            if not cells or "License #" in cells:
                                continue
                            if len(cells) >= 6:
                                lic_num, name, address, row_city, zip_code = cells[1], cells[2], cells[3], cells[4], cells[5]
                                phone = cells[6] if len(cells) > 6 else ""
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
                    if records:
                        log(f"ZenRows retrieved {len(records)} California CSLB records.")
                        return records

            return []
        except Exception as exc:
            log(f"California ZenRows fallback exception: {exc}", "warning")
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
