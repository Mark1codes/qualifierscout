from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright
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
    - 'JOHNSON, ROBERT CRAIG' -> contractor_name='Robert Craig Johnson', company_name='JOHNSON, ROBERT CRAIG'
    - 'AGILE CONSTRUCTION SERVICES LLC' -> contractor_name='', company_name='AGILE CONSTRUCTION SERVICES LLC'
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
        log("Opening Nevada NSCB license search with Playwright...")
        records = await self._try_public_search(request, log)
        if not records:
            log("Nevada NSCB returned no records or search timed out.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_nevada_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_type_code = NV_LICENSE_TYPES.get(request.license_type, NV_LICENSE_TYPES["default"])
        city_param = (request.city or "").upper().strip()
        county_code = NV_COUNTIES.get(city_param, NV_COUNTIES["default"])

        records = []
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**self._browser_launch_options())
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1920, "height": 1080}
                )
                page = await context.new_page()
                await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

                log(f"Navigating to Nevada NSCB search page for Classification={lic_type_code}...")
                await page.goto(SEARCH_URL, timeout=30000)

                # Select County and Classification
                log(f"Filling search form: County Code={county_code}, Classification={lic_type_code}...")
                await page.select_option("#ContentPlaceHolder1_County", value=county_code)
                await page.select_option("#ContentPlaceHolder1_App", value=lic_type_code)

                log("Submitting search request...")
                await page.click("#ContentPlaceHolder1_btnSearch")

                await page.wait_for_load_state("networkidle")
                await page.wait_for_timeout(3000)

                content = await page.content()
                soup = BeautifulSoup(content, "html.parser")

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

                # Up to max_records, fetch Principal/Qualifier details if contractor_name is empty
                needed_records = records[: request.max_records]
                corporate_count = sum(1 for r in needed_records if not r["contractor_name"])
                if corporate_count > 0:
                    log(f"Fetching Officers & Qualifiers from NSCB detail pages for {corporate_count} corporate records...")
                    
                    for idx, r in enumerate(needed_records):
                        if r["contractor_name"]:
                            continue
                        try:
                            lic_num = r["license_number"]
                            link = page.locator(f"a:has-text('{lic_num}')")
                            if await link.count() == 0:
                                link = page.locator("a[id*='lnkLicense']").nth(idx)
                                
                            if await link.count() > 0:
                                await link.first.click()
                                try:
                                    await page.wait_for_selector("text=License Details", timeout=6000)
                                except Exception:
                                    await page.wait_for_timeout(2000)
                                
                                detail_soup = BeautifulSoup(await page.content(), "html.parser")
                                lines = [l.strip() for l in detail_soup.get_text("\n", strip=True).split("\n") if l.strip()]
                                
                                qualifier_name = ""
                                principal_name = ""
                                in_principal = False
                                in_qualifier = False
                                
                                for line in lines:
                                    if line == "Principal Name":
                                        in_principal, in_qualifier = True, False
                                        continue
                                    elif line in ("Qualified Individual(s)", "Qualified Individual"):
                                        in_principal, in_qualifier = False, True
                                        continue
                                    elif line in ("Bond", "Bond Type:", "Classification(s):", "Status:"):
                                        in_principal, in_qualifier = False, False
                                        
                                    if in_principal and line not in ("Relation Description", "Principal Name", "Manager", "Member", "President", "Secretary", "Treasurer", "Director", "Officer", "Owner"):
                                        if ("," in line or re.match(r"^[A-Z\s\.\-]{3,}$", line)) and not principal_name:
                                            principal_name = line
                                            
                                    if in_qualifier and line not in ("Qualifier Type", "Qualified Individual(s)", "Qualified Individual", "CMS and Trade", "Trade", "CMS"):
                                        if ("," in line or re.match(r"^[A-Z\s\.\-]{3,}$", line)) and not qualifier_name:
                                            qualifier_name = line
                                            
                                person_raw = qualifier_name or principal_name
                                if person_raw:
                                    r["contractor_name"] = format_person_name(person_raw)
                                    log(f"  [{lic_num}] Found Officer/Qualifier: '{r['contractor_name']}'")
                                    
                                back_btn = page.locator("#ContentPlaceHolder1_btnBack, input[value*='Back']")
                                if await back_btn.count() > 0:
                                    await back_btn.first.click()
                                    try:
                                        await page.wait_for_selector("#ContentPlaceHolder1_dtgResults", timeout=6000)
                                    except Exception:
                                        await page.wait_for_timeout(2000)
                                else:
                                    await page.goto(SEARCH_URL)
                                    await page.select_option("#ContentPlaceHolder1_County", value=county_code)
                                    await page.select_option("#ContentPlaceHolder1_App", value=lic_type_code)
                                    await page.click("#ContentPlaceHolder1_btnSearch")
                                    await page.wait_for_selector("#ContentPlaceHolder1_dtgResults")
                        except Exception as e:
                            log(f"  Detail fetch skipped for license {r.get('license_number')}: {e}", "warning")
                            try:
                                await page.goto(SEARCH_URL)
                                await page.select_option("#ContentPlaceHolder1_County", value=county_code)
                                await page.select_option("#ContentPlaceHolder1_App", value=lic_type_code)
                                await page.click("#ContentPlaceHolder1_btnSearch")
                                await page.wait_for_selector("#ContentPlaceHolder1_dtgResults")
                            except Exception:
                                pass

                log(f"Extracted {len(records)} Nevada records.")
                await browser.close()
                return records

        except Exception as exc:
            log(f"Nevada NSCB scrape failed ({type(exc).__name__}): {exc}", "error")
            return []

    @staticmethod
    def _browser_launch_options() -> dict[str, Any]:
        options: dict[str, Any] = {"headless": True}
        return options
