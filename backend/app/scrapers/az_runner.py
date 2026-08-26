import sys
import asyncio
import json
import re
from pathlib import Path
from playwright.async_api import async_playwright

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception:
        pass

SEARCH_URL = "https://azroc.my.site.com/AZRoc/s/contractor-search"

def clean_person_name(raw_name: str) -> str:
    if not raw_name:
        return ""
    clean = re.sub(r"\([^)]*\)", "", raw_name).strip()
    return clean.title()

def parse_apex_record(item: dict, requested_license_type: str) -> list[dict]:
    results = []
    acc_name = (item.get("accountName") or "").strip()
    dba_name = (item.get("accountDbaName") or "").strip()
    phone = (item.get("phone") or "").strip()

    if dba_name.upper().startswith("DBA :"):
        dba_name = dba_name[5:].strip()

    company_name = acc_name or dba_name

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
            lic_num = (lic.get("licenseNo") or lic.get("licenseNumber") or "").strip()
            sub_type = (lic.get("subType") or "").strip()
            lic_class = (lic.get("licenseClassification") or "").strip()
            lic_desc = (lic.get("licenseDescription") or "").strip()
            lic_status = (lic.get("status") or lic.get("licenseStatus") or "").strip()

            full_lic_type = sub_type or f"{lic_class} {lic_desc}".strip() or requested_license_type
            status = "Active" if lic_status.upper() == "ACTIVE" else (lic_status.capitalize() or "Active")

            formatted_lic_num = lic_num if lic_num.startswith("ROC") else f"ROC {lic_num}" if lic_num else ""

            results.append({
                "source_url": SEARCH_URL,
                "contractor_name": contractor_name,
                "company_name": company_name,
                "license_number": formatted_lic_num,
                "license_type": full_lic_type,
                "license_status": status,
                "expiration_date": "",
                "address": address_raw,
                "city": city,
                "state": state,
                "zip_code": zip_code,
                "phone": phone,
            })

    return results

async def run_arizona_search(search_keyword: str, requested_license_type: str):
    captured_records = []
    try:
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
                                        parsed_items = parse_apex_record(item, requested_license_type)
                                        captured_records.extend(parsed_items)
                    except Exception:
                        pass

            page.on("response", handle_response)
            await page.goto(SEARCH_URL, wait_until="domcontentloaded", timeout=30000)

            search_input = page.locator("input[placeholder*='search terms']")
            await search_input.wait_for(state="visible", timeout=15000)
            await search_input.fill(search_keyword)
            await page.wait_for_timeout(500)

            await search_input.press("Enter")
            try:
                search_btn = page.locator("button:has-text('Search')").first
                await search_btn.click(timeout=3000)
            except Exception:
                pass

            for _ in range(12):
                await page.wait_for_timeout(1000)
                if len(captured_records) >= 10:
                    break

            await browser.close()
    except Exception as e:
        sys.stderr.write(f"Runner Playwright Error: {e}\n")

    return captured_records

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(json.dumps([]))
        sys.exit(0)

    keyword = sys.argv[1]
    license_type = sys.argv[2]

    results = asyncio.run(run_arizona_search(keyword, license_type))
    print(json.dumps(results))
