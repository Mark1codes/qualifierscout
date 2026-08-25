import sys
import asyncio
import json
import re
from pathlib import Path

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

from playwright.async_api import async_playwright
from app.schemas import ScrapeStartRequest

BASE_URL = "https://azroc.my.site.com"
SEARCH_URL = f"{BASE_URL}/AZRoc/s/contractor-search"


def clean_person_name(raw_name: str) -> str:
    """
    Clean contact name string returned by Arizona ROC Apex:
    e.g. 'Tyler Michael Robbins (Qualifying Party) ' -> 'Tyler Michael Robbins'
    'Michael A Robbins (Manager;Member) ' -> 'Michael A Robbins'
    """
    if not raw_name:
        return ""
    clean = re.sub(r"\([^)]*\)", "", raw_name).strip()
    return clean.title()


AZ_TRADE_MAP = {
    "A-4 Drilling": "DRILLING",
    "A-4": "DRILLING",
    "Well Drilling Contractor": "DRILLING",
    "General Contractor": "General",
    "Electrical Contractor": "Electric",
    "Plumbing Contractor": "Plumbing",
    "HVAC Contractor": "HVAC",
    "Roofing Contractor": "Roofing",
    "Solar Contractor": "Solar",
}


class ArizonaScraper:
    """Scraper implementation for Arizona Registrar of Contractors (AZ ROC)."""

    name = "Arizona ROC"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        """Execute license search for Arizona contractors."""
        log("Opening Arizona Registrar of Contractors (AZ ROC) search portal...")

        records = await self._try_playwright_search(request, log)
        if not records:
            log("AZ ROC search returned zero records or timed out.", "warning")

        # If user explicitly requested A-4 Drilling, strictly filter for A-4 license classification
        if "A-4" in request.license_type:
            a4_only = [r for r in records if "A-4" in r.get("license_type", "").upper() or "DRILLING" in r.get("license_type", "").upper()]
            log(f"Filtered {len(a4_only)} strict Drilling license records from {len(records)} raw results.")
            if a4_only:
                records = a4_only

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_arizona_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_playwright_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Use Playwright to render search form and capture Apex response data."""
        captured_records = []

        try:
            city_query = (request.city or "").upper().strip()
            mapped_trade = AZ_TRADE_MAP.get(request.license_type, request.license_type)

            # Prioritize trade query when selected
            if request.license_type not in ("default", "General Contractor"):
                search_keyword = mapped_trade
            elif city_query:
                search_keyword = city_query
            else:
                search_keyword = mapped_trade if mapped_trade != "default" else "Contractor"

            log(f"Searching Arizona ROC portal for '{search_keyword}'...")

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = await context.new_page()

                async def handle_response(response):
                    if "getRecords" in response.url or "ARCP_ContractorSearch" in response.url:
                        try:
                            data = await response.json()
                            if isinstance(data, dict) and "actions" in data:
                                for action in data["actions"]:
                                    if action.get("state") == "SUCCESS" and "returnValue" in action:
                                        for item in action["returnValue"]:
                                            parsed_items = self._parse_apex_record(item, request.license_type)
                                            captured_records.extend(parsed_items)
                        except Exception:
                            pass

                page.on("response", handle_response)

                await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)

                search_input = page.locator("input[placeholder*='search terms']")
                await search_input.wait_for(state="visible", timeout=15000)
                await search_input.fill(search_keyword)
                await page.wait_for_timeout(500)

                # Press Enter and click Search button
                await search_input.press("Enter")
                try:
                    search_btn = page.locator("button:has-text('Search')").first
                    await search_btn.click(timeout=3000)
                except Exception:
                    pass

                # Poll for up to 10 seconds for Apex response arrival
                for _ in range(10):
                    await page.wait_for_timeout(1000)
                    if len(captured_records) >= 10:
                        break

                await browser.close()

            log(f"Extracted {len(captured_records)} Arizona ROC contractor records.")
            return captured_records

        except Exception as exc:
            log(f"Arizona Playwright search failed: {exc}", "error")
            return []

    def _parse_apex_record(self, item: dict, requested_license_type: str) -> list[dict]:
        """Parse raw Arizona Apex record into QualifierScout lead formats for each license held."""
        results = []
        acc_name = (item.get("accountName") or "").strip()
        dba_name = (item.get("accountDbaName") or "").strip()
        phone = (item.get("phone") or "").strip()

        if dba_name.upper().startswith("DBA :"):
            dba_name = dba_name[5:].strip()

        company_name = acc_name or dba_name

        # Extract contact personnel (Qualifying Party / Officer / Member)
        contractor_name = ""
        contacts = item.get("accountContactData") or []
        for contact in contacts:
            raw_cname = contact.get("contactName") or ""
            cleaned = clean_person_name(raw_cname)
            if cleaned:
                if "Qualifying Party" in raw_cname or "Member" in raw_cname or not contractor_name:
                    contractor_name = cleaned
                    if "Qualifying Party" in raw_cname:
                        break

        # Location parsing
        address_raw = item.get("address") or ""
        city = ""
        state = "AZ"
        zip_code = ""

        if address_raw:
            parts = [p.strip() for p in address_raw.split(",")]
            if len(parts) >= 1:
                city = parts[0].title()
            if len(parts) >= 2:
                state = parts[1].upper() or "AZ"
            if len(parts) >= 3:
                zip_code = parts[2]

        lic_data = item.get("licenseData") or []
        if not lic_data:
            if company_name:
                results.append({
                    "source_url": SEARCH_URL,
                    "contractor_name": contractor_name,
                    "company_name": company_name,
                    "license_number": "",
                    "license_type": requested_license_type,
                    "license_status": "Active",
                    "expiration_date": "",
                    "address": address_raw,
                    "city": city,
                    "state": state,
                    "zip_code": zip_code,
                    "phone": phone,
                })
        else:
            for lic in lic_data:
                lic_no = lic.get("licenseNo") or ""
                lic_status = lic.get("status") or "Active"
                lic_type = lic.get("subType") or requested_license_type
                qp_name = (lic.get("qpName") or "").title()
                final_contractor = contractor_name or qp_name

                results.append({
                    "source_url": SEARCH_URL,
                    "contractor_name": final_contractor,
                    "company_name": company_name,
                    "license_number": lic_no,
                    "license_type": lic_type,
                    "license_status": lic_status,
                    "expiration_date": "",
                    "address": address_raw,
                    "city": city,
                    "state": state,
                    "zip_code": zip_code,
                    "phone": phone,
                })

        return results
