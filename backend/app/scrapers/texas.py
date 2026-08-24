import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest

BASE_URL = "https://www.tdlr.texas.gov/LicenseSearch/"
SEARCH_URL = BASE_URL + "LicenseSearch.asp"
RESULTS_URL = BASE_URL + "SearchResultsListBrowse.asp"

# Texas TDLR does NOT issue General Contractor licenses (TX has no statewide GC license).
# Regulated contractor types with COMPANY-level licenses on TDLR:
#   AIRREF  = Air Conditioning / Refrigeration Contractors (TACLA prefix) — B2B goldmine
#   ELCTRC  = Electrical Contractors (company license, EEC prefix)
#   ELEVTR  = Elevator Contractors (company license)
#   MOLD    = Mold Remediation Companies
#   WWDRLL  = Water Well Drillers / Pump Installers
# Texas Department of Licensing and Regulation (TDLR) Search Mapping
TX_LICENSE_TYPE_MAP = {
    "HVAC Contractor": "AIRREF",
    "Electrical Contractor": "ELCTRC",
    "Elevator Contractor": "ELEVTR",
    "Mold Remediation Contractor": "MOLD",
    "Water Well Driller": "WWDRLL",
    "default": "AIRREF",
}


class TexasScraper:
    """Scraper implementation for Texas Department of Licensing and Regulation (TDLR)."""

    name = "Texas TDLR"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        """Execute license search for Texas contractors."""
        log("Opening Texas TDLR license search...")

        if request.license_type in ("General Contractor", "Residential Contractor", "Building Contractor"):
            log(
                f"NOTE: Texas has no statewide '{request.license_type}' license. "
                f"Scraping HVAC Contractor company licenses (TACLA) as the closest regulated equivalent.",
                "warning",
            )

        records = await self._try_public_search(request, log)
        if not records:
            log("Live source returned no records or blocked the request.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_texas_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            tdlr_type = TX_LICENSE_TYPE_MAP.get(request.license_type, TX_LICENSE_TYPE_MAP["default"])
            city = (request.city or "Houston").upper().strip()

            async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
                r = await client.get(SEARCH_URL)
                soup = BeautifulSoup(r.content, "html.parser")
                form = soup.find("form")
                action = form.get("action", "") if form else ""
                full_url = urljoin(BASE_URL, action) if action else RESULTS_URL

                # TDLR city search field requires 20-character padding
                city_padded = city.ljust(20)

                payload = {
                    "pht_lic": "",
                    "pht_expdt": "",
                    "pht_oth_name": "",
                    "phy_zip": "",
                    "B1": "Search",
                    "B2": "Reset",
                    "tdlr_status": tdlr_type,
                    "phy_city": city_padded,
                    "phy_cnty": "-1",
                }

                log(f"Searching Texas TDLR for {tdlr_type} contractors in {city}...")
                r2 = await client.post(full_url, data=payload)
                soup2 = BeautifulSoup(r2.content, "html.parser")

                records = self._parse_results(soup2, city, request.license_type, log)

                # Fallback to statewide query if city search yields zero records
                if not records:
                    log(f"No results for {city}. Trying statewide search...", "warning")
                    payload["phy_city"] = ""
                    r3 = await client.post(full_url, data=payload)
                    soup3 = BeautifulSoup(r3.content, "html.parser")
                    records = self._parse_results(soup3, "", request.license_type, log)

                log(f"Found {len(records)} records in Texas.")
                return records

        except Exception as exc:
            log(f"Texas TDLR scrape failed: {exc}", "error")
            return []

    def _parse_results(self, soup: BeautifulSoup, city: str, license_type: str, log) -> list[dict]:
        """Parse HTML result tables into structured contractor lead objects."""
        records = []

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            header_found = False

            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if not cells:
                    continue

                # Detect header row
                if "License#" in cells[0] or "License#" in str(cells):
                    header_found = True
                    continue

                if not header_found or len(cells) < 3:
                    continue

                lic_num = cells[0].strip()
                exp_date_raw = cells[1].strip() if len(cells) > 1 else ""
                name_raw = cells[2].strip() if len(cells) > 2 else ""
                row_city = cells[3].strip() if len(cells) > 3 else city
                row_zip = cells[4].strip() if len(cells) > 4 else ""
                county = cells[5].strip() if len(cells) > 5 else ""
                phone = cells[6].strip() if len(cells) > 6 else ""

                if not lic_num or not name_raw or not re.match(r"[A-Z]+\d+", lic_num):
                    continue

                exp_date = ""
                status = "Active"
                exp_match = re.search(r"(\d{2}/\d{2}/\d{4})", exp_date_raw)
                if exp_match:
                    exp_date = exp_match.group(1)
                    if "Expired" in exp_date_raw:
                        status = "Expired"
                elif "Ren process" in exp_date_raw:
                    status = "Renewal Pending"

                contractor_name = ""
                company_name = ""

                paren_match = re.match(r"^(.*?)\s*\((.+)\)\s*$", name_raw)
                if paren_match:
                    raw_person = paren_match.group(1).strip()
                    company_name = paren_match.group(2).strip()

                    parts = [p.strip() for p in raw_person.split(",") if p.strip()]
                    contractor_name = f"{parts[1]} {parts[0]}" if len(parts) == 2 else raw_person
                else:
                    parts = [p.strip() for p in name_raw.split(",") if p.strip()]
                    contractor_name = f"{parts[1]} {parts[0]}" if len(parts) == 2 else name_raw

                if company_name and company_name.upper().strip() in {"NONE", "N/A", "NA", "SAME AS ABOVE", "SELF"}:
                    company_name = ""

                records.append({
                    "source_url":       BASE_URL,
                    "contractor_name":  contractor_name,
                    "company_name":     company_name,
                    "license_number":   lic_num,
                    "license_type":     license_type,
                    "license_status":   status,
                    "expiration_date":  exp_date,
                    "city":             row_city.title() or city.title(),
                    "state":            "TX",
                    "zip_code":         row_zip,
                    "phone":            phone,
                })

        return records
