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

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_florida_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            # Pick license type code
            lic_type_code = FL_LICENSE_TYPES.get(request.license_type, FL_LICENSE_TYPES["default"])
            city = (request.city or "MIAMI").upper()

            async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
                log("Fetching DBPR session tokens...")
                r = await client.get(f"{SEARCH_URL}?mode=0&SID=")
                soup = BeautifulSoup(r.content, 'html.parser')
                form = soup.find('form')
                if not form:
                    log("Failed to load Florida DBPR initial form.", "error")
                    return []

                data = {tag.get('name'): tag.get('value', '') for tag in form.find_all('input', type='hidden')}
                data["SearchType"] = "City"

                log("Initializing City search mode...")
                r2 = await client.post(f"{SEARCH_URL}?mode=1&SID=&brd=&typ=", data=data)
                soup2 = BeautifulSoup(r2.content, 'html.parser')
                form2 = soup2.find('form')
                if not form2:
                    log("Failed to load Florida DBPR mode-1 form.", "error")
                    return []

                data2 = {tag.get('name'): tag.get('value', '') for tag in form2.find_all('input', type='hidden')}
                data2["Board"] = "06"             # Construction Industry
                data2["LicenseType"] = lic_type_code
                data2["hBoard"] = "06"
                data2["hLicTyp"] = lic_type_code
                data2["City"] = city
                data2["County"] = ""
                data2["State"] = "FL"
                data2["RecsPerPage"] = "50"

                log(f"Searching Florida for {request.license_type} in {city}...")
                r3 = await client.post(
                    f"{SEARCH_URL}?mode=2&search=City&SID=&brd=06&typ={lic_type_code}",
                    data=data2
                )
                soup3 = BeautifulSoup(r3.content, 'html.parser')

                records = self._parse_results(soup3, city, lic_type_code, log)

                # Also search adjacent license types if we got nothing or want more
                if not records:
                    log(f"No results for type {lic_type_code}, trying General Contractor (0605)...", "warning")
                    data2["LicenseType"] = "0605"
                    data2["hLicTyp"] = "0605"
                    r4 = await client.post(
                        f"{SEARCH_URL}?mode=2&search=City&SID=&brd=06&typ=0605",
                        data=data2
                    )
                    soup4 = BeautifulSoup(r4.content, 'html.parser')
                    records = self._parse_results(soup4, city, "0605", log)

                log(f"Found {len(records)} Florida records.")
                return records

        except Exception as exc:
            log(f"Florida DBPR scrape failed: {exc}", "error")
            return []

    def _parse_results(self, soup: BeautifulSoup, city: str, lic_type: str, log) -> list[dict]:
        """
        Parse the DBPR results page. Each contractor takes multiple rows:
        Row pattern per contractor:
          - License Type | Name | NameType | License#/Rank | Status/Expires
          - Address row(s)

        FL DBPR licenses are issued to BUSINESSES (not individuals).
        The 'Primary' NameType row contains the official business/company name.
        The qualifier (individual person) is NOT available on the list page.

        Strategy:
        - 'Primary' row Name  -> company_name  (used for Apollo triangulation)
        - contractor_name     -> blank (Apollo will search by company name)
        - 'DBA' / 'Alternate' rows -> SKIP (avoids duplicate records per license)
        """
        records = []
        seen_licenses = set()  # Prevent duplicate entries for DBA/Alternate rows

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            for i, row in enumerate(rows):
                cells = [c.get_text(strip=True) for c in row.find_all("td")]
                # We want rows with 5 cells: LicenseType, Name, NameType, LicNum, Status
                if len(cells) != 5:
                    continue
                lic_type_val, name, name_type, lic_num, status_expires = cells

                # Only process the PRIMARY name row (official business name)
                # Skip DBA and Alternate rows — they are aliases for the same license
                if name_type != "Primary":
                    continue
                if not name or not lic_type_val:
                    continue

                # Parse license number (format like "CBC1256976Cert Building" -> "CBC1256976")
                lic_num_clean = re.match(r"([A-Z0-9]+)", lic_num)
                lic_num_clean = lic_num_clean.group(1) if lic_num_clean else lic_num

                # Skip already-seen licenses (extra safety for duplicates)
                if lic_num_clean in seen_licenses:
                    continue
                seen_licenses.add(lic_num_clean)

                # Look ahead for address row
                address = ""
                zip_code = ""
                if i + 1 < len(rows):
                    next_cells = [c.get_text(strip=True) for c in rows[i+1].find_all("td")]
                    if len(next_cells) >= 2 and "Address" in next_cells[0]:
                        address_str = next_cells[1]
                        address = address_str.split("  ")[0].strip()
                        zip_match = re.search(r'FL\s+(\d{5})', address_str)
                        if zip_match:
                            zip_code = zip_match.group(1)

                # Parse status and expiration
                # "Current, Active08/31/2028" -> status=Active, expiry=08/31/2028
                status = "Active" if "Active" in status_expires else (
                    "Expired" if "Void" in status_expires or "Expired" in status_expires else status_expires
                )
                exp_match = re.search(r"(\d{2}/\d{2}/\d{4})", status_expires)
                expiration = exp_match.group(1) if exp_match else ""

                # FL licenses are issued to BUSINESSES. The 'name' from the Primary row
                # IS the company name. contractor_name is left blank intentionally —
                # Apollo will use company_name for the triangulation query.
                company_name = name
                contractor_name = ""

                records.append({
                    "source_url": BASE_URL,
                    "license_type": lic_type_val,
                    "contractor_name": contractor_name,
                    "company_name": company_name,
                    "license_number": lic_num_clean,
                    "license_status": status,
                    "expiration_date": expiration,
                    "address": address,
                    "city": city.title(),
                    "state": "FL",
                    "zip_code": zip_code,
                })

        return records
