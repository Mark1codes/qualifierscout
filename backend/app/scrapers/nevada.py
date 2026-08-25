from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest

# Nevada State Contractors Board classification IDs
NV_LICENSE_TYPES = {
    "General Contractor": "100844",        # B General Building
    "Building Contractor": "100844",       # B General Building
    "Residential Contractor": "100844",    # B General Building
    "General Engineering": "100212",       # A General Engineering
    "Electrical Contractor": "101528",     # C02 Electrical
    "HVAC Contractor": "100857",           # C21 Refrigeration and Air Conditioning
    "Plumbing Contractor": "101527",       # C01 Plumbing and Heating
    "Roofing Contractor": "100850",        # C15 Roofing and Siding
    "Underground Contractor": "100212",    # A General Engineering / Underground
    "default": "100844"
}

# Nevada County IDs
NV_COUNTIES = {
    "CARSON CITY": "100340",
    "CHURCHILL": "100341",
    "CLARK": "100342",
    "LAS VEGAS": "100342",
    "HENDERSON": "100342",
    "NORTH LAS VEGAS": "100342",
    "DOUGLAS": "100343",
    "ELKO": "100344",
    "ESMERALDA": "100345",
    "EUREKA": "100346",
    "HUMBOLDT": "100347",
    "LANDER": "100348",
    "LINCOLN": "100349",
    "LYON": "100350",
    "MINERAL": "100351",
    "NYE": "100352",
    "PERSHING": "100353",
    "STOREY": "100354",
    "WASHOE": "100355",
    "RENO": "100355",
    "SPARKS": "100355",
    "WHITE PINE": "100356",
    "ALL": "0",
    "default": "0"
}

NV_CITIES = [
    "LAS VEGAS", "NORTH LAS VEGAS", "HENDERSON", "RENO", "SPARKS", "CARSON CITY", 
    "ELKO", "BOULDER CITY", "MESQUITE", "FALLON", "FERNLEY", "PAHRUMP", 
    "INCLINE VILLAGE", "DAYTON", "SPRING CREEK", "GARDNERVILLE", "MINDEN"
]

BASE_URL = "https://app.nvcontractorsboard.com"
SEARCH_URL = f"{BASE_URL}/Clients/nvscb/Public/ContractorListing/ListingSearch.aspx"

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
    "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
    "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
    "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
    "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
}


def format_person_name(raw: str) -> str:
    """Converts 'KHALEEL, MOHAMMAD HISHAM' or 'MOHAMMAD HISHAM KHALEEL' into 'Mohammad Hisham Khaleel'."""
    clean = raw.strip()
    if not clean:
        return ""
    if "," in clean:
        parts = [p.strip() for p in clean.split(",", 1)]
        clean = f"{parts[1]} {parts[0]}".strip()
    return " ".join([w.capitalize() for w in clean.split()])


def parse_nevada_name(name_raw: str) -> tuple[str, str]:
    """
    Parses a Nevada NSCB name string into (contractor_name, company_name).
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
            contractor_name = format_person_name(f"{first_part} {last_part}")
            return contractor_name, name_clean

    if is_corporate:
        return "", name_clean
    else:
        return format_person_name(name_clean), name_clean


class NevadaScraper:
    name = "Nevada NSCB"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Querying Nevada NSCB portal directly (100% Free, Instant execution)...")
        records = await self._try_public_search(request, log)
        if not records:
            log("Nevada NSCB returned no records or search returned empty.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_nevada_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_type_code = NV_LICENSE_TYPES.get(request.license_type, NV_LICENSE_TYPES["default"])
        city_param = (request.city or "").upper().strip()
        county_code = NV_COUNTIES.get(city_param, NV_COUNTIES["default"])

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Referer": SEARCH_URL
        }

        records = []
        try:
            async with httpx.AsyncClient(timeout=25.0, follow_redirects=True) as client:
                log(f"Querying Nevada NSCB search page for Classification={lic_type_code}, County={county_code}...")
                r1 = await client.get(SEARCH_URL, headers=headers)
                if r1.status_code != 200:
                    log(f"Nevada GET status code {r1.status_code}", "warning")
                    return []

                soup1 = BeautifulSoup(r1.text, "html.parser")
                viewstate = soup1.find("input", id="__VIEWSTATE")
                viewstate_gen = soup1.find("input", id="__VIEWSTATEGENERATOR")
                event_val = soup1.find("input", id="__EVENTVALIDATION")

                vs = viewstate.get("value") if viewstate else ""
                vsg = viewstate_gen.get("value") if viewstate_gen else ""
                ev = event_val.get("value") if event_val else ""

                form_data = {
                    "__VIEWSTATE": vs,
                    "__VIEWSTATEGENERATOR": vsg,
                    "__EVENTVALIDATION": ev,
                    "ctl00$ContentPlaceHolder1$County": county_code,
                    "ctl00$ContentPlaceHolder1$App": lic_type_code,
                    "ctl00$ContentPlaceHolder1$btnSearch": "Search"
                }

                log("Submitting Nevada search form request...")
                r2 = await client.post(SEARCH_URL, data=form_data, headers=headers)
                if r2.status_code != 200:
                    log(f"Nevada POST search returned status code {r2.status_code}", "error")
                    return []

                soup = BeautifulSoup(r2.text, "html.parser")
                tables = soup.find_all("table")
                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                        if len(cells) < 3:
                            continue

                        cell1, cell2, cell3 = cells[0], cells[1], cells[2]
                        if "License #" not in cell2 and "Classifications" not in cell2:
                            continue

                        # Extract cell1: Name + Address + City + State + Zip
                        m1 = re.search(r"^(.*?)\s+(NV)\s+(\d{5}(?:-\d{4})?)$", cell1, re.I)
                        if m1:
                            prefix = m1.group(1).strip()
                            state = m1.group(2).strip().upper()
                            zip_code = m1.group(3).strip()

                            row_city = ""
                            for c in NV_CITIES:
                                if prefix.endswith(" " + c) or prefix == c:
                                    row_city = c.title()
                                    prefix = prefix[:-len(c)].strip()
                                    break

                            m_addr = re.search(r"^(.*?)\s+(\d+.*)$", prefix)
                            if m_addr:
                                raw_name = m_addr.group(1).strip()
                                address = m_addr.group(2).strip()
                            else:
                                raw_name = prefix
                                address = ""
                        else:
                            raw_name = cell1
                            address, row_city, state, zip_code = "", city_param.title(), "NV", ""

                        contractor_name, company_name = parse_nevada_name(raw_name)

                        # Extract cell2: License #, Phone, Classification
                        m_lic = re.search(r"License\s*\#?\s*:\s*([A-Za-z0-9]+)", cell2, re.I)
                        lic_num = m_lic.group(1) if m_lic else ""

                        m_phone = re.search(r"(\(\d{3}\)\s*\d{3}-\d{4}|\d{3}-\d{3}-\d{4})", cell2)
                        phone = m_phone.group(1) if m_phone else ""

                        m_class = re.search(r"Classifications\s*:\s*(.*)", cell2, re.I)
                        lic_class = m_class.group(1).strip() if m_class else request.license_type

                        # Strict filtering for Plumbing Contractors: Keep C-1D & C-1 (Full), exclude sub-codes like C-1A (Boiler), C-1H (Water Heaters), etc.
                        if "Plumb" in request.license_type:
                            upper_class = lic_class.upper()
                            is_c1d = bool(re.search(r"\bC-?1D\b", upper_class))
                            is_c1_full = bool(re.search(r"\bC-?1\s+PLUMBING\b", upper_class)) and not bool(re.search(r"\bC-?1[A-CE-Z]\b", upper_class))
                            has_limitation = bool(re.search(r"\b(RADON|FIRE|SOLAR|SHEET METAL|INSULATION|BOILER)\b", upper_class))

                            if not ((is_c1d or is_c1_full) and not has_limitation):
                                continue

                        # Extract cell3: Expires, Status
                        m_exp = re.search(r"Expires\s*:\s*(\d{2}-\d{2}-\d{4}|\d{2}/\d{2}/\d{4})", cell3, re.I)
                        expiration = m_exp.group(1) if m_exp else ""

                        status = "Active" if "Active" in cell3 else ("Expired" if "Expired" in cell3 else "Active")

                        if not lic_num:
                            continue

                        records.append({
                            "source_url": SEARCH_URL,
                            "contractor_name": contractor_name,
                            "company_name": company_name,
                            "license_number": lic_num,
                            "license_type": lic_class,
                            "license_status": status,
                            "expiration_date": expiration,
                            "address": address,
                            "city": row_city or city_param.title() or "Las Vegas",
                            "state": "NV",
                            "zip_code": zip_code,
                            "phone": phone
                        })

                log(f"Extracted {len(records)} strict Nevada records from NSCB.")
                return records

        except Exception as exc:
            log(f"Nevada NSCB scrape failed ({type(exc).__name__}): {exc}", "error")
            return []
