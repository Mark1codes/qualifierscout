from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest


# Florida DBPR License types for Construction Industry (Board 06)
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
            return "", name_clean
        else:
            contractor_name = f"{first_part} {last_part}".strip().title()
            return contractor_name, name_clean

    if is_corporate:
        return "", name_clean
    else:
        return name_clean.title(), name_clean


def format_person_name(name_raw: str) -> str:
    """
    Formats DBPR detail page personnel name strings:
    - 'MONTERO, RICARDO (Primary Name)' -> 'Ricardo Montero'
    - 'SCHWAB, JOHN RICHARD' -> 'John Richard Schwab'
    """
    if not name_raw:
        return ""
    clean = name_raw.replace("(Primary Name)", "").strip()
    if "," in clean:
        parts = [p.strip() for p in clean.split(",", 1)]
        last = parts[0].title()
        first = parts[1].title() if len(parts) > 1 else ""
        return f"{first} {last}".strip()
    return clean.title()


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
        """Direct HTTP search against Florida DBPR with parallel personnel enrichment."""
        try:
            lic_type_code = FL_LICENSE_TYPES.get(request.license_type, FL_LICENSE_TYPES["default"])
            city = (request.city or "MIAMI").upper()

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Referer": f"{SEARCH_URL}?mode=0&search=City",
                "Origin": BASE_URL,
                "Content-Type": "application/x-www-form-urlencoded"
            }

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                log(f"Querying Florida DBPR directly for {request.license_type} in {city}...")
                
                # Step 1: Initializing session on mode=1
                mode1_url = f"{SEARCH_URL}?mode=1&search=City&SID=&brd=&typ="
                r1 = await client.get(mode1_url, headers=headers)
                if r1.status_code != 200:
                    log(f"Florida DBPR mode=1 returned status {r1.status_code}", "warning")
                    return await self._try_zenrows_search(request, log)

                soup1 = BeautifulSoup(r1.text, "html.parser")
                form1 = soup1.find("form")
                if not form1:
                    log("Florida DBPR mode=1 search form not found", "warning")
                    return await self._try_zenrows_search(request, log)

                data = {tag.get('name'): tag.get('value', '') for tag in form1.find_all('input', type='hidden') if tag.get('name')}
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

                # Step 2: Submitting search to mode=2
                mode2_url = f"{SEARCH_URL}?mode=2&search=City&SID=&brd=06&typ={lic_type_code}"
                r2 = await client.post(mode2_url, headers=headers, data=data)
                if r2.status_code != 200:
                    log(f"Florida DBPR mode=2 returned status {r2.status_code}", "warning")
                    return await self._try_zenrows_search(request, log)

                soup2 = BeautifulSoup(r2.text, "html.parser")
                raw_items = self._parse_results(soup2, city, log)
                if not raw_items:
                    log("Florida DBPR direct search returned 0 records, attempting ZenRows fallback...", "warning")
                    return await self._try_zenrows_search(request, log)

                log(f"Extracted {len(raw_items)} initial Florida search records. Resolving individual contractor personnel names...")

                # Parallel resolution of individual qualifier / contractor personnel names
                async def resolve_personnel(item):
                    contractor_name = item["contractor_name"]
                    company_name = item["company_name"]
                    detail_href = item.get("detail_href")

                    if not contractor_name and detail_href:
                        person_name, dba_name = await self._fetch_personnel_name(client, detail_href, headers)
                        if person_name:
                            contractor_name = person_name
                        if dba_name and not company_name:
                            company_name = dba_name

                    return {
                        "source_url": BASE_URL,
                        "license_type": item["license_type"],
                        "contractor_name": contractor_name,
                        "company_name": company_name or item["raw_name"],
                        "license_number": item["license_number"],
                        "license_status": item["license_status"],
                        "expiration_date": item["expiration_date"],
                        "address": item["address"],
                        "city": item["city"],
                        "state": "FL",
                        "zip_code": item["zip_code"],
                    }

                records = await asyncio.gather(*[resolve_personnel(item) for item in raw_items[: request.max_records]])
                log(f"Successfully retrieved {len(records)} Florida DBPR records with individual contractor names.")
                return records

        except Exception as exc:
            log(f"Florida DBPR direct search failed: {exc}, switching to ZenRows fallback...", "warning")
            return await self._try_zenrows_search(request, log)

    async def _fetch_personnel_name(self, client: httpx.AsyncClient, detail_href: str, headers: dict) -> tuple[str, str]:
        """Fetches individual qualifier / contractor personnel name from Florida DBPR license detail page."""
        if not detail_href:
            return "", ""
        try:
            url = detail_href if detail_href.startswith("http") else f"{BASE_URL}/{detail_href.lstrip('/')}"
            r = await client.get(url, headers=headers, timeout=10.0)
            if r.status_code != 200:
                return "", ""

            soup = BeautifulSoup(r.text, "html.parser")
            primary_name = ""
            dba_name = ""
            for row in soup.find_all("div", class_="form-group"):
                label = row.find("label")
                val_div = row.find("div", class_="col-sm-8")
                if label and val_div:
                    lbl_text = label.get_text(strip=True).lower()
                    val_text = val_div.get_text(strip=True)
                    if lbl_text == "name":
                        primary_name = format_person_name(val_text)
                    elif "dba" in lbl_text:
                        dba_name = val_text.strip()

            return primary_name, dba_name
        except Exception:
            return "", ""

    async def _try_zenrows_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Preserved ZenRows fallback strategy for future subscription use."""
        try:
            import os
            import random
            zenrows_api_key = os.getenv("ZENROWS_API_KEY", "").strip()
            if not zenrows_api_key:
                log("ZenRows API key not configured.", "info")
                return []

            zr_url = "https://api.zenrows.com/v1/"
            lic_type_code = FL_LICENSE_TYPES.get(request.license_type, FL_LICENSE_TYPES["default"])
            city = (request.city or "MIAMI").upper()

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }

            for attempt in range(3):
                session_id = str(random.randint(10000, 99999))
                session_params = {
                    "apikey": zenrows_api_key,
                    "js_render": "true",
                    "premium_proxy": "true",
                    "proxy_country": "us",
                    "session_id": session_id
                }

                async with httpx.AsyncClient(timeout=90) as client:
                    p1 = dict(session_params)
                    p1["url"] = f"{SEARCH_URL}?mode=1&search=City&SID=&brd=&typ="
                    r1 = await client.get(zr_url, params=p1)
                    if r1.status_code != 200:
                        continue

                    soup1 = BeautifulSoup(r1.text, 'html.parser')
                    form1 = soup1.find("form")
                    if not form1:
                        continue

                    data = {tag.get('name'): tag.get('value', '') for tag in form1.find_all('input', type='hidden') if tag.get('name')}
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

                    p2 = dict(session_params)
                    p2["url"] = f"{SEARCH_URL}?mode=2&search=City&SID=&brd=06&typ={lic_type_code}"
                    r2 = await client.post(zr_url, params=p2, data=data)
                    if r2.status_code == 200:
                        soup2 = BeautifulSoup(r2.text, 'html.parser')
                        raw_items = self._parse_results(soup2, city, log)
                        if raw_items:
                            records = []
                            for item in raw_items[: request.max_records]:
                                contractor_name = item["contractor_name"]
                                company_name = item["company_name"]
                                if not contractor_name and item.get("detail_href"):
                                    person_name, dba_name = await self._fetch_personnel_name(client, item["detail_href"], headers)
                                    if person_name:
                                        contractor_name = person_name
                                    if dba_name and not company_name:
                                        company_name = dba_name

                                records.append({
                                    "source_url": BASE_URL,
                                    "license_type": item["license_type"],
                                    "contractor_name": contractor_name,
                                    "company_name": company_name or item["raw_name"],
                                    "license_number": item["license_number"],
                                    "license_status": item["license_status"],
                                    "expiration_date": item["expiration_date"],
                                    "address": item["address"],
                                    "city": item["city"],
                                    "state": "FL",
                                    "zip_code": item["zip_code"],
                                })
                            log(f"ZenRows retrieved {len(records)} Florida records.")
                            return records

            return []
        except Exception as exc:
            log(f"ZenRows fallback exception: {exc}", "warning")
            return []

    def _parse_results(self, soup: BeautifulSoup, city: str, log) -> list[dict]:
        """
        Parse the DBPR results page table rows, including detail links.
        """
        raw_items = []
        seen_licenses = set()
        current = None

        for row in soup.find_all("tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            cell_texts = [c.get_text(strip=True) for c in cells]
            
            # Contractor Header Row: 5 cells
            if len(cell_texts) == 5 and cell_texts[0] != "License Type":
                lic_type_val, name, name_type, lic_num, status_expires = cell_texts
                
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

                detail_href = ""
                a_tag = cells[1].find("a")
                if a_tag and a_tag.get("href"):
                    detail_href = a_tag.get("href")

                current = {
                    "raw_name": name,
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
                    "detail_href": detail_href
                }
                raw_items.append(current)
                
            # Address Row: 2 cells, starting with "Address" or "Location"
            elif len(cell_texts) == 2 and current:
                label = cell_texts[0].lower()
                val = cell_texts[1].strip()
                if "address*:" in label:
                    address_str = val
                    address_base = address_str.split("  ")[0].strip()
                    if not current["address"]:
                        current["address"] = address_base
                    
                    zip_match = re.search(r'FL\s+(\d{5})', address_str)
                    if zip_match and not current["zip_code"]:
                        current["zip_code"] = zip_match.group(1)

        return raw_items
