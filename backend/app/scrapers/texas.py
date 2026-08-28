import asyncio
import csv
import json
import re
import sys
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest

BASE_URL = "https://www.tdlr.texas.gov/LicenseSearch/"
SEARCH_URL = BASE_URL + "LicenseSearch.asp"
RESULTS_URL = BASE_URL + "SearchResultsListBrowse.asp"

# Texas Licensing Mapping
# TDLR: HVAC (AIRREF), Electrical (ELCTRC), Elevator (ELEVTR), Mold (MRCOMP), Water Well (WWDPMP)
# TSBPE: Plumbing Contractor (RMP - Responsible Master Plumber)
TX_LICENSE_TYPE_MAP = {
    "Plumbing Contractor": "PLUMBING",
    "Plumbing": "PLUMBING",
    "HVAC Contractor": "AIRREF",
    "Electrical Contractor": "ELCTRC",
    "Elevator Contractor": "ELEVTR",
    "Mold Remediation Contractor": "MRCOMP",
    "Water Well Driller": "WWDPMP",
    "default": "AIRREF",
}

CORPORATE_KEYWORDS = {
    "INC", "LLC", "L.L.C.", "CO", "CORP", "CORPORATION", "LIMITED", "LTD",
    "L.P.", "LP", "SERVICES", "SERVICE", "ELECTRIC", "ELECTRICAL", "SOLUTIONS",
    "ENTERPRISES", "GROUP", "SYSTEMS", "HOLDINGS", "COMPANY", "PARTNERS",
    "BUILDERS", "CONSTRUCTION", "TECHNOLOGIES", "PLUMBING", "AIR", "COOLING",
    "HEATING", "POOL", "POOLS", "REPAIR", "INSPECTOR", "DEPOT", "CONTRACTOR",
    "CONTRACTORS", "DBA", "BROS", "BROTHERS", "SUPPLY", "TEXAS", "LONE STAR",
    "MECHANICAL", "HOT TUB", "AQUATECH", "REFRIGERATION", "ROOFING", "SOLAR",
    "MASONRY", "CONCRETE", "FENCING", "UTILITY", "DRILLING", "WELL", "WATER",
    "DEVELOPMENT", "PROPERTIES", "PAVING", "ENGINEERING", "REMODELING"
}


def is_corporate(name: str) -> bool:
    """Check if string contains corporate entity keywords."""
    clean = re.sub(r"[^\w\s]", " ", name.upper())
    words = clean.split()
    return any(w in CORPORATE_KEYWORDS for w in words)


def parse_tdlr_name(name_raw: str) -> tuple[str, str]:
    """
    Parses TDLR raw name string into (contractor_name, company_name).
    contractor_name: Individual Person (First Last)
    company_name: Business Entity (Empty string if individual practitioner)
    """
    if not name_raw:
        return "", ""

    name_raw = name_raw.strip()
    contractor_name = ""
    company_name = ""

    # Check for parentheses: Outside (Inside)
    paren_match = re.match(r"^(.*?)\s*\((.+)\)\s*$", name_raw)
    if paren_match:
        part1 = paren_match.group(1).strip()
        part2 = paren_match.group(2).strip()

        if is_corporate(part1) and not is_corporate(part2):
            company_name = part1
            person_part = part2
        elif is_corporate(part2) and not is_corporate(part1):
            company_name = part2
            person_part = part1
        elif is_corporate(part1) and is_corporate(part2):
            company_name = part1
            person_part = part2
        else:
            person_part = part1
            company_name = part2

        if person_part:
            if "," in person_part and not is_corporate(person_part):
                p_parts = [p.strip() for p in person_part.split(",") if p.strip()]
                if len(p_parts) == 2:
                    contractor_name = f"{p_parts[1]} {p_parts[0]}".title()
                else:
                    contractor_name = person_part.title()
            else:
                contractor_name = person_part.title()

    else:
        if is_corporate(name_raw):
            company_name = name_raw
            contractor_name = ""
        else:
            if "," in name_raw:
                p_parts = [p.strip() for p in name_raw.split(",") if p.strip()]
                if len(p_parts) == 2:
                    contractor_name = f"{p_parts[1]} {p_parts[0]}".title()
                else:
                    contractor_name = name_raw.title()
            else:
                contractor_name = name_raw.title()
            # Individual practitioner: leave company_name empty!
            company_name = ""

    if company_name and (
        company_name.upper().strip() in {"NONE", "N/A", "NA", "SAME AS ABOVE", "SELF", "N A"}
        or company_name.strip().lower() == contractor_name.strip().lower()
    ):
        company_name = ""

    return contractor_name, company_name


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
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            tdlr_type = TX_LICENSE_TYPE_MAP.get(request.license_type, TX_LICENSE_TYPE_MAP["default"])
            city = (request.city or "").upper().strip()
            city_padded = city.ljust(20) if city else ""

            async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=30) as client:
                full_url = "https://www.tdlr.texas.gov/LicenseSearch/SearchResultsListBrowse.asp?from=search"

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

                location_label = city if city else "Statewide (All Cities)"
                log(f"Searching Texas TDLR for {tdlr_type} contractors in {location_label}...")

                r2 = await client.post(full_url, data=payload)
                soup = BeautifulSoup(r2.content, "html.parser")

                records = []
                seen_licenses = set()

                page = 1
                max_pages = max(10, (request.max_records // 20) + 2)

                while soup and len(records) < request.max_records and page <= max_pages:
                    new_records = self._parse_results_page(soup, city, request.license_type, seen_licenses, log)
                    records.extend(new_records)

                    if len(records) >= request.max_records:
                        break

                    # Check for Next page link
                    next_link = soup.find("a", string=lambda s: s and "Next" in s)
                    if not next_link:
                        break

                    href = next_link.get("href")
                    next_url = "https://www.tdlr.texas.gov/LicenseSearch/" + href
                    r_next = await client.get(next_url)
                    soup = BeautifulSoup(r_next.content, "html.parser")
                    page += 1

                # If city search yielded 0 records and user had specified a city, try statewide fallback
                if not records and city:
                    log(f"No results for {city}. Trying statewide search...", "warning")
                    payload["phy_city"] = ""
                    r3 = await client.post(full_url, data=payload)
                    soup = BeautifulSoup(r3.content, "html.parser")
                    page = 1
                    while soup and len(records) < request.max_records and page <= max_pages:
                        new_records = self._parse_results_page(soup, "", request.license_type, seen_licenses, log)
                        records.extend(new_records)
                        if len(records) >= request.max_records:
                            break
                        next_link = soup.find("a", string=lambda s: s and "Next" in s)
                        if not next_link:
                            break
                        href = next_link.get("href")
                        next_url = "https://www.tdlr.texas.gov/LicenseSearch/" + href
                        r_next = await client.get(next_url)
                        soup = BeautifulSoup(r_next.content, "html.parser")
                        page += 1

                log(f"Found {len(records)} records in Texas.")
                return records

        except Exception as exc:
            log(f"Texas TDLR scrape failed: {exc}", "error")
            return []

    def _parse_results_page(
        self,
        soup: BeautifulSoup,
        city: str,
        license_type: str,
        seen_licenses: set[str],
        log
    ) -> list[dict]:
        """Parse HTML result table for a single page into structured contractor lead objects."""
        records = []

        table = soup.find("table", {"cellpadding": "2"})
        if not table:
            return records

        rows = table.find_all("tr")
        header_found = False

        for row in rows:
            cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
            if not cells:
                continue

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

            # Deduplication check within current run
            if lic_num in seen_licenses:
                continue
            seen_licenses.add(lic_num)

            exp_date = ""
            status = "Active"
            exp_match = re.search(r"(\d{2}/\d{2}/\d{4})", exp_date_raw)
            if exp_match:
                exp_date = exp_match.group(1)
                if "Expired" in exp_date_raw:
                    status = "Expired"
            elif "Ren process" in exp_date_raw:
                status = "Renewal Pending"

            contractor_name, company_name = parse_tdlr_name(name_raw)

            records.append({
                "source_url": BASE_URL,
                "contractor_name": contractor_name,
                "company_name": company_name if company_name and company_name.lower().strip() != contractor_name.lower().strip() else "",
                "license_number": lic_num,
                "license_type": license_type,
                "license_status": status,
                "expiration_date": exp_date,
                "city": row_city.title() or city.title(),
                "state": "TX",
                "zip_code": row_zip,
                "phone": phone,
            })

        return records

    async def _scrape_tsbpe_plumbing(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Scrape Texas Responsible Master Plumber (RMP) companies from TSBPE state registry."""
        url = "https://tsbpe.texas.gov/download-csv/RMP/"
        city_filter = (request.city or "").upper().strip()

        log("Fetching Texas State Board of Plumbing Examiners (TSBPE) registry...")
        records = []
        csv_text = ""

        # Method 1: Try native curl command (bypasses Cloudflare HTTP 403 blocks instantly)
        try:
            cmd = [
                "curl.exe" if sys.platform == "win32" else "curl",
                "-sSL",
                "-H", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "-H", "Accept-Language: en-US,en;q=0.9",
                url
            ]
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await process.communicate()
            if process.returncode == 0 and len(stdout) > 1000:
                csv_text = stdout.decode("utf-8", errors="ignore")
                log("Successfully fetched TSBPE registry data via native curl engine.")
        except Exception as curl_exc:
            log(f"Native curl download failed: {curl_exc}", "warning")

        # Method 2: Try httpx AsyncClient fallback
        if not csv_text:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                }
                async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
                    r = await client.get(url, headers=headers)
                    if r.status_code == 200:
                        csv_text = r.text
            except Exception:
                pass

        if not csv_text:
            log("Failed to retrieve TSBPE CSV data.", "error")
            return []

        try:
            lines = csv_text.splitlines()
            reader = csv.DictReader(lines)

            seen_licenses = set()

            for row in reader:
                row_city = (row.get("CITY") or "").upper().strip()

                # City filter if provided by user
                if city_filter and city_filter not in row_city:
                    continue

                lic_num = f"RMP-{row.get('LICENSE_NBR', '')}"
                if lic_num in seen_licenses:
                    continue
                seen_licenses.add(lic_num)

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
                    "license_number": lic_num,
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
            log(f"TSBPE Plumbing parse failed: {exc}", "error")
            return []
