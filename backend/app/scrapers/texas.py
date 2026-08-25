import asyncio
import csv
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

# Texas Licensing Mapping
# TDLR: HVAC (AIRREF), Electrical (ELCTRC), Elevator (ELEVTR), Mold (MOLD), Water Well (WWDRLL)
# TSBPE: Plumbing Contractor (RMP - Responsible Master Plumber)
TX_LICENSE_TYPE_MAP = {
    "Plumbing Contractor": "PLUMBING",
    "Plumbing": "PLUMBING",
    "HVAC Contractor": "AIRREF",
    "Electrical Contractor": "ELCTRC",
    "Elevator Contractor": "ELEVTR",
    "Mold Remediation Contractor": "MOLD",
    "Water Well Driller": "WWDRLL",
    "default": "AIRREF",
}


class TexasScraper:
    """Scraper implementation for Texas licensing (TDLR & TSBPE)."""

    name = "Texas Licensing"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        """Execute license search for Texas contractors."""
        log("Opening Texas contractor license portal...")

        if "Plumb" in request.license_type:
            records = await self._scrape_tsbpe_plumbing(request, log)
        else:
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
                full_url = "https://www.tdlr.texas.gov/LicenseSearch/SearchResultsListBrowse.asp?from=search"

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
                if any("License" in c for c in cells):
                    header_found = True
                    continue

                if not header_found or len(cells) < 3:
                    continue

                lic_num = cells[0].strip()
                exp_date_raw = cells[1].strip() if len(cells) > 1 else ""
                name_raw = cells[2].strip() if len(cells) > 2 else ""
                row_city = cells[3].strip() if len(cells) > 3 else city
                row_zip = cells[4].strip() if len(cells) > 4 else ""
                phone = cells[6].strip() if len(cells) > 6 else ""

                if not lic_num or not name_raw or not re.search(r"[A-Za-z]+.*?\d+", lic_num):
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
                    "company_name":     company_name or contractor_name,
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

    async def _scrape_tsbpe_plumbing(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Scrape Texas Responsible Master Plumber (RMP) companies from TSBPE state registry."""
        url = "https://tsbpe.texas.gov/download-csv/RMP/"
        city_filter = (request.city or "").upper().strip()

        log("Fetching Texas State Board of Plumbing Examiners (TSBPE) registry...")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        }

        records = []
        csv_text = ""
        try:
            async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    csv_text = r.text
                else:
                    log(f"TSBPE direct endpoint returned status code {r.status_code}. Bypassing with Playwright stealth...", "warning")

            if not csv_text:
                from playwright.async_api import async_playwright
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(user_agent=headers["User-Agent"])
                    page = await context.new_page()
                    try:
                        async with page.expect_download(timeout=20000) as download_info:
                            try:
                                await page.goto(url, timeout=20000)
                            except Exception as goto_err:
                                if "Download is starting" not in str(goto_err):
                                    raise goto_err

                        download = await download_info.value
                        temp_path = self.raw_dir / f"tsbpe_temp_{request.city or 'all'}.csv"
                        await download.save_as(temp_path)
                        csv_text = temp_path.read_text(encoding="utf-8", errors="ignore")
                        if temp_path.exists():
                            temp_path.unlink()
                    finally:
                        await browser.close()

            if not csv_text:
                log("Failed to retrieve TSBPE CSV data.", "error")
                return []

            lines = csv_text.splitlines()
            reader = csv.DictReader(lines)

            for row in reader:
                row_city = (row.get("CITY") or "").upper().strip()

                # City filter if provided by user
                if city_filter and city_filter not in row_city:
                    continue

                first = (row.get("FIRST_NAME") or "").strip().title()
                last = (row.get("LAST_NAME") or "").strip().title()
                full_name = f"{first} {last}".strip()
                company = (row.get("PLUMB_COMPANY") or "").strip().title() or full_name

                raw_status = (row.get("LIC_STATUS") or "").strip()
                status = "Active" if raw_status == "Current" else (raw_status.capitalize() or "Active")

                records.append({
                    "source_url": "https://tsbpe.texas.gov/download-csv/RMP/",
                    "contractor_name": full_name,
                    "company_name": company,
                    "license_number": f"RMP-{row.get('LICENSE_NBR', '')}",
                    "license_type": "Plumbing Contractor",
                    "license_status": status,
                    "expiration_date": row.get("EXPIRATION_DTE", ""),
                    "address": f"{row.get('ADDR1', '')} {row.get('ADDR2', '')}".strip(),
                    "city": row.get("CITY", "").title(),
                    "state": "TX",
                    "zip_code": row.get("ZIP", ""),
                    "phone": row.get("PHONE", ""),
                    "title": "Responsible Master Plumber / Owner",
                })

            log(f"Extracted {len(records)} Texas Plumbing Contractor records from TSBPE.")
            return records

        except Exception as exc:
            log(f"TSBPE Plumbing scrape failed: {exc}", "error")
            return []
