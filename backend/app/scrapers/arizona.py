import asyncio
import json
import re
from pathlib import Path

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
    # Strip parentheses and roles
    clean = re.sub(r"\([^)]*\)", "", raw_name).strip()
    return clean.title()


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

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_arizona_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_playwright_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        """Use Playwright to render search form and capture Apex response data."""
        captured_records = []

        try:
            city_query = (request.city or "PHOENIX").upper().strip()
            trade_query = request.license_type if request.license_type != "default" else "Contractor"
            search_keyword = f"{city_query}" if request.city else f"{trade_query}"

            log(f"Searching Arizona ROC portal for '{search_keyword}'...")

            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = await context.new_page()

                await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)

                search_input = page.locator("input[placeholder*='search terms']")
                await search_input.fill(search_keyword)
                await page.wait_for_timeout(500)

                # Expect Apex response when submitting search
                async with page.expect_response(
                    lambda r: "getRecords" in r.url or "ARCP_ContractorSearch" in r.url,
                    timeout=15000,
                ) as resp_info:
                    await search_input.press("Enter")

                response = await resp_info.value
                data = await response.json()

                if isinstance(data, dict) and "actions" in data:
                    for action in data["actions"]:
                        if action.get("state") == "SUCCESS" and "returnValue" in action:
                            for item in action["returnValue"]:
                                parsed = self._parse_apex_record(item, request.license_type)
                                if parsed:
                                    captured_records.append(parsed)

                await browser.close()

            log(f"Extracted {len(captured_records)} Arizona ROC contractor records.")
            return captured_records

        except Exception as exc:
            log(f"Arizona Playwright search failed: {exc}", "error")
            return []

    def _parse_apex_record(self, item: dict, requested_license_type: str) -> dict | None:
        """Parse raw Arizona Apex record into QualifierScout lead format compatible with Apollo."""
        acc_name = (item.get("accountName") or "").strip()
        dba_name = (item.get("accountDbaName") or "").strip()
        phone = (item.get("phone") or "").strip()

        # Clean DBA name prefix if present (e.g. 'DBA : Phoenix Plumbing' -> 'Phoenix Plumbing')
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
                # Prioritize Qualifying Party or Member
                if "Qualifying Party" in raw_cname or "Member" in raw_cname or not contractor_name:
                    contractor_name = cleaned
                    if "Qualifying Party" in raw_cname:
                        break

        # License details
        lic_data = item.get("licenseData") or []
        lic_no = ""
        lic_status = "Active"
        lic_type = requested_license_type

        if lic_data:
            first_lic = lic_data[0]
            lic_no = first_lic.get("licenseNo") or ""
            lic_status = first_lic.get("status") or "Active"
            lic_type = first_lic.get("subType") or requested_license_type
            if first_lic.get("qpName") and not contractor_name:
                contractor_name = first_lic.get("qpName").title()

        # Location parsing (e.g. 'Apache Junction, AZ, 85120')
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

        if not lic_no and not company_name:
            return None

        return {
            "source_url": SEARCH_URL,
            "contractor_name": contractor_name,
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
        }
