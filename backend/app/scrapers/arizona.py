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
        """Use Playwright via isolated subprocess runner to capture Apex response data."""
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

            for attempt in range(1, 4):
                log(f"Searching Arizona ROC portal for '{search_keyword}' (Attempt {attempt}/3)...")
                runner_path = Path(__file__).parent / "az_runner.py"
                cmd = [sys.executable, str(runner_path), search_keyword, request.license_type]

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await process.communicate()

                if process.returncode != 0:
                    err_msg = stderr.decode('utf-8', errors='ignore').strip()
                    log(f"Arizona runner process attempt {attempt} failed with code {process.returncode}: {err_msg}", "warning")
                    await asyncio.sleep(2)
                    continue

                raw_out = stdout.decode("utf-8", errors="ignore").strip()
                start_idx = raw_out.find("[")
                end_idx = raw_out.rfind("]")
                if start_idx != -1 and end_idx != -1 and end_idx >= start_idx:
                    json_str = raw_out[start_idx:end_idx+1]
                    captured_records = json.loads(json_str)
                    if captured_records:
                        log(f"Extracted {len(captured_records)} Arizona ROC contractor records.")
                        return captured_records
                
                log(f"Arizona runner attempt {attempt} returned empty or invalid payload. Retrying...", "warning")
                await asyncio.sleep(2)

            log("Arizona Playwright search failed after 3 attempts.", "error")
            return []

        except Exception as exc:
            log(f"Arizona Playwright search exception: {exc}", "error")
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
