from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest


# Florida DBPR License types for Construction Industry (Board 06)
# Only contractor-relevant types
FL_LICENSE_TYPES = {
    "General Contractor": "0605",        # Certified General Contractor
    "Building Contractor": "0602",        # Certified Building Contractor
    "Residential Contractor": "0608",     # Certified Residential Contractor
    "Roofing Contractor": "0603",         # Certified Roofing Contractor
    "Electrical Contractor": "0605",      # fallback to General
    "HVAC Contractor": "0601",            # Certified AC Contractor
    "Plumbing Contractor": "0604",        # Certified Plumbing Contractor
    "default": "0605",                    # Certified General Contractor
}

BASE_URL = "https://www.myfloridalicense.com"
SEARCH_URL = f"{BASE_URL}/wl11.asp"

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
    "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
    "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
    "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
    "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
}


def parse_florida_name(name_raw: str) -> tuple[str, str]:
    """
    Parses a Florida DBPR name string into (contractor_name, company_name).
    - 'Acosta, Daniel David' -> contractor_name='Daniel David Acosta', company_name='Acosta, Daniel David'
    - 'Ajce Corporation' -> contractor_name='', company_name='Ajce Corporation'
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
            # Company with comma, e.g. "Ace Construction & Remodeling, Llc"
            return "", name_clean
        else:
            # Individual in LAST, FIRST format, e.g. "Acosta, Daniel David"
            contractor_name = f"{first_part} {last_part}".strip()
            return contractor_name, name_clean

    if is_corporate:
        return "", name_clean
    else:
        return name_clean, name_clean


class FloridaScraper:
    name = "Florida DBPR"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Opening Florida DBPR license search...")
        records = await self._try_public_search(request, log)
        if not records:
            log("Live source returned no records or blocked the request.", "warning")

        if request.individuals_only:
            records = [r for r in records if r.get("contractor_name")]

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_florida_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            import os
            import random
            zenrows_api_key = os.getenv("ZENROWS_API_KEY", "82dafe2655912ea0fd4b57ce1dd6e437838cdb2f")
            zr_url = "https://api.zenrows.com/v1/"
            
            lic_type_code = FL_LICENSE_TYPES.get(request.license_type, FL_LICENSE_TYPES["default"])
            city = (request.city or "MIAMI").upper()

            # Retry a few times with different session_ids if it fails
            for attempt in range(5):
                session_id = str(random.randint(10000, 99999))
                session_params = {
                    "apikey": zenrows_api_key,
                    "js_render": "true",
                    "premium_proxy": "true",
                    "proxy_country": "us",
                    "session_id": session_id
                }

                async with httpx.AsyncClient(timeout=90) as client:
                    log(f"Attempt {attempt+1}: Initializing ZenRows session on mode=1...")
                    p1 = dict(session_params)
                    p1["url"] = f"{SEARCH_URL}?mode=1&search=City&SID=&brd=&typ="
                    
                    try:
                        r1 = await client.get(zr_url, params=p1)
                        if r1.status_code != 200:
                            log(f"mode=1 returned {r1.status_code}, retrying...", "warning")
                            continue
                            
                        soup1 = BeautifulSoup(r1.text, 'html.parser')
                        form1 = soup1.find("form")
                        if not form1:
                            log("mode=1 form not found, retrying...", "warning")
                            continue
                            
                        data = {tag.get('name'): tag.get('value', '') for tag in form1.find_all('input', type='hidden') if tag.get('name')}
                        
                        if 'hSearchType' not in data:
                            log("mode=1 returned the home page instead of search form, retrying...", "warning")
                            continue
                            
                        data.update({
                            "Board": "06",
                            "LicenseType": lic_type_code,
                            "hBoard": "06",
                            "hLicTyp": lic_type_code,
                            "City": city,
                            "County": "",
                            "State": "FL",
                            "RecsPerPage": "50",
                            "SearchGo": "Search"
                        })
                        
                        log(f"Submitting search to mode=2 for {request.license_type} in {city}...")
                        p2 = dict(session_params)
                        p2["url"] = f"{SEARCH_URL}?mode=2&search=City&SID=&brd=06&typ={lic_type_code}"
                        
                        r2 = await client.post(zr_url, params=p2, data=data)
                        if r2.status_code != 200:
                            log(f"mode=2 returned {r2.status_code}, retrying...", "warning")
                            continue
                            
                        soup2 = BeautifulSoup(r2.text, 'html.parser')
                        records = self._parse_results(soup2, city, lic_type_code, log)
                        
                        if records:
                            log(f"Successfully retrieved {len(records)} Florida records.")
                            return records
                            
                    except Exception as e:
                        log(f"Attempt {attempt+1} failed: {e}", "warning")
                        continue
                        
            log("Florida DBPR scrape exhausted all retries.", "error")
            return []

        except Exception as exc:
            log(f"Florida DBPR scrape failed: {type(exc).__name__} - {exc}", "error")
            return []

    def _parse_results(self, soup: BeautifulSoup, city: str, lic_type: str, log) -> list[dict]:
        """
        Parse the DBPR results page. Each contractor takes multiple rows.
        FL DBPR licenses are issued to BUSINESSES (not individuals).
        The 'Primary' NameType row contains the official business/company name.
        """
        records = []
        seen_licenses = set()
        current = None

        # Iterate through all rows globally to handle nested table structures correctly
        for row in soup.find_all("tr"):
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"], recursive=False)]
            
            # Contractor Header Row: 5 cells
            if len(cells) == 5 and cells[0] != "License Type":
                lic_type_val, name, name_type, lic_num, status_expires = cells
                
                # Ensure we have a valid name
                if not name:
                    continue

                lic_num_clean = re.match(r"([A-Z]+[0-9]+)", lic_num)
                lic_num_clean = lic_num_clean.group(1) if lic_num_clean else lic_num

                if lic_num_clean in seen_licenses:
                    current = None
                    continue
                seen_licenses.add(lic_num_clean)
                
                status = "Active" if "Active" in status_expires else (
                    "Expired" if "Void" in status_expires or "Expired" in status_expires else status_expires
                )
                exp_match = re.search(r"(\d{2}/\d{2}/\d{4})", status_expires)
                expiration = exp_match.group(1) if exp_match else ""

                contractor_name, company_name = parse_florida_name(name)

                current = {
                    "source_url": BASE_URL,
                    "license_type": lic_type_val,
                    "contractor_name": contractor_name,
                    "company_name": company_name,
                    "license_number": lic_num_clean,
                    "license_status": status,
                    "expiration_date": expiration,
                    "address": "",
                    "city": city.title(),
                    "state": "FL",
                    "zip_code": "",
                }
                records.append(current)
                
            # Address Row: 2 cells, starting with "Address" or "Location"
            elif len(cells) == 2 and current:
                label = cells[0].lower()
                val = cells[1].strip()
                if "address*:" in label:
                    address_str = val
                    address_base = address_str.split("  ")[0].strip()
                    if not current["address"]:
                        current["address"] = address_base
                    
                    zip_match = re.search(r'FL\s+(\d{5})', address_str)
                    if zip_match and not current["zip_code"]:
                        current["zip_code"] = zip_match.group(1)

        return records
