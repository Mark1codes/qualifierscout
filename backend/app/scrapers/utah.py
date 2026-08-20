from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any
import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright

from app.schemas import ScrapeStartRequest

UT_LICENSE_TYPES = {
    "General Contractor": "B100",
    "Building Contractor": "B100",
    "Residential Contractor": "R100",
    "General Engineering": "E100",
    "Underground Contractor": "E100",
    "Electrical Contractor": "E200",
    "HVAC Contractor": "S210",
    "Plumbing Contractor": "S270",
    "Roofing Contractor": "S280",
    "default": "B100"
}

BASE_URL = "https://secure.utah.gov"
SEARCH_URL = f"{BASE_URL}/llv/search/index.html"
SCRAPERAPI_KEY = os.environ.get("SCRAPERAPI_KEY", "")
ZENROWS_API_KEY = os.environ.get("ZENROWS_API_KEY", "")

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
    "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
    "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
    "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
    "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
}


def format_person_name(raw: str) -> str:
    clean = raw.strip()
    if not clean:
        return ""
    if "," in clean:
        parts = [p.strip() for p in clean.split(",", 1)]
        clean = f"{parts[1]} {parts[0]}".strip()
    return " ".join([w.capitalize() for w in clean.split()])


def parse_utah_name(name_raw: str) -> tuple[str, str]:
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


class UtahScraper:
    name = "Utah DOPL"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        records = []
        if ZENROWS_API_KEY:
            log("Attempting primary live scrape via ZenRows anti-bot bypass...", "info")
            records = await self._scrape_zenrows(request, log)

        if not records:
            log("Opening Utah DOPL license search portal via Playwright...", "info")
            records = await self._scrape_playwright(request, log)

        if not records and SCRAPERAPI_KEY:
            log("Attempting HTTP fallback via ScraperAPI...", "info")
            records = await self._try_scraperapi_search(request, log)

        if not records:
            log("Utah DOPL search returned no records.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_utah_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _scrape_playwright(self, request: ScrapeStartRequest, log) -> list[dict]:
        def _run_in_thread():
            import sys
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(self._scrape_playwright_internal(request, log))

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run_in_thread)
        except RuntimeError:
            return await self._scrape_playwright_internal(request, log)

    async def _scrape_playwright_internal(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_code = UT_LICENSE_TYPES.get(request.license_type, UT_LICENSE_TYPES["default"])
        user_query = request.city.strip().upper() if request.city else ""
        
        # Build list of search terms
        search_terms = ["CONSTRUCTION", "BUILDING", "CONTRACTOR", "ROOFING", "PLUMBING", "ELECTRIC", "LLC", "INC"]
        if user_query and user_query not in search_terms:
            search_terms.insert(0, user_query)

        records = []
        seen_licenses = set()

        try:
            async with async_playwright() as p:
                log("Launching Playwright browser for Utah DOPL...")
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--disable-blink-features=AutomationControlled", "--no-sandbox"]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1366, "height": 768}
                )

                for query in search_terms:
                    if len(records) >= request.max_records:
                        break

                    log(f"Querying Utah DOPL for '{query}'...")
                    page = await context.new_page()

                    # Stealth evasion script
                    await page.add_init_script("""
                        Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                        Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                    """)

                    try:
                        await page.goto(SEARCH_URL, wait_until="networkidle", timeout=30000)
                        await page.fill("#fullName", query)

                        # Select CONTAINING searchType radio button (startsWith=false)
                        rad = await page.query_selector("#containing")
                        if rad:
                            await rad.click()

                        # Check Contractor checkbox (item38 / value=207)
                        cb = await page.query_selector("#item38")
                        if cb:
                            await cb.click()

                        # Execute captchaSubmit() or grecaptcha
                        has_captcha = await page.evaluate("typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function'")
                        if has_captcha:
                            sitekey_elem = await page.query_selector("#recaptchaSiteKey")
                            sitekey = await sitekey_elem.get_attribute("value") if sitekey_elem else "6LcQUqIUAAAAAG7lgG1BfDlhvVUuFP26QsY4Eq6_"
                            try:
                                token = await page.evaluate(f"grecaptcha.execute('{sitekey}', {{action: 'search'}})")
                                await page.evaluate(f"""(tok) => {{
                                    if (window.setProfessions) window.setProfessions();
                                    const respName = document.getElementById("g-recaptcha-response-name");
                                    if (respName) respName.value = tok;
                                    document.getElementById("searchByNameForm").submit();
                                }}""", token)
                            except Exception:
                                await page.evaluate("""() => {
                                    if (window.setProfessions) window.setProfessions();
                                    if (window.captchaSubmit) {
                                        window.captchaSubmit(document.getElementById("searchByNameForm"));
                                    } else {
                                        document.getElementById("searchByNameForm").submit();
                                    }
                                }""")
                        else:
                            await page.evaluate("""() => {
                                if (window.setProfessions) window.setProfessions();
                                document.getElementById("searchByNameForm").submit();
                            }""")

                        try:
                            await page.wait_for_load_state("networkidle", timeout=15000)
                        except Exception:
                            pass
                        await page.wait_for_timeout(3000)

                        soup = BeautifulSoup(await page.content(), "html.parser")
                        tables = soup.find_all("table")

                        for table in tables:
                            rows = table.find_all("tr")
                            for row in rows:
                                cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                                if len(cells) < 4:
                                    continue

                                if "License" in cells[0] or "Name" in cells[0]:
                                    continue

                                raw_name = cells[0]
                                lic_num = cells[1]
                                lic_type = cells[2]
                                status = cells[3] if len(cells) > 3 else "Active"

                                if not lic_num or lic_num.lower() == "license number" or lic_num in seen_licenses:
                                    continue

                                seen_licenses.add(lic_num)
                                contractor_name, company_name = parse_utah_name(raw_name)

                                records.append({
                                    "source_url": SEARCH_URL,
                                    "contractor_name": contractor_name,
                                    "company_name": company_name,
                                    "license_number": lic_num,
                                    "license_type": lic_type or f"{request.license_type} ({lic_code})",
                                    "license_status": status,
                                    "expiration_date": "",
                                    "address": "",
                                    "city": request.city.title() if request.city else "Salt Lake City",
                                    "state": "UT",
                                    "zip_code": "",
                                    "phone": ""
                                })

                    except Exception as exc:
                        log(f"Error scraping Utah term '{query}': {exc}", "warning")
                    finally:
                        await page.close()

                await browser.close()

        except Exception as exc:
            log(f"Playwright Utah scrape failed ({type(exc).__name__}): {exc}", "error")

        return records

    async def _scrape_zenrows(self, request: ScrapeStartRequest, log) -> list[dict]:
        user_query = request.city.strip().upper() if request.city else ""
        search_terms = ["CONSTRUCTION", "BUILDING", "CONTRACTOR", "ROOFING", "PLUMBING", "ELECTRIC", "LLC", "INC"]
        if user_query and user_query not in search_terms:
            search_terms.insert(0, user_query)

        records = []
        seen_licenses = set()
        zenrows_endpoint = "https://api.zenrows.com/v1/"

        async with httpx.AsyncClient(timeout=60.0) as client:
            for query in search_terms:
                if len(records) >= request.max_records:
                    break

                log(f"ZenRows Bypass: Querying Utah DOPL for '{query}'...")
                js_instructions = json.dumps([
                    {"fill": ["#fullName", query]},
                    {"click": "#containing"},
                    {"click": "#item38"},
                    {"evaluate": "if (window.setProfessions) window.setProfessions(); if (window.captchaSubmit) { window.captchaSubmit(document.getElementById('searchByNameForm')); } else { document.getElementById('searchByNameForm').submit(); }"},
                    {"wait": 5000}
                ])

                params = {
                    "apikey": ZENROWS_API_KEY,
                    "url": SEARCH_URL,
                    "js_render": "true",
                    "premium_proxy": "true",
                    "proxy_country": "us",
                    "js_instructions": js_instructions
                }

                try:
                    resp = await client.get(zenrows_endpoint, params=params)
                    if resp.status_code != 200:
                        log(f"ZenRows API response status {resp.status_code}: {resp.text[:150]}", "warning")
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")
                    tables = soup.find_all("table")

                    for table in tables:
                        rows = table.find_all("tr")
                        for row in rows:
                            cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                            if len(cells) < 4 or "License" in cells[0] or "Name" in cells[0]:
                                continue

                            raw_name = cells[0]
                            lic_num = cells[1]
                            lic_type = cells[2]
                            status = cells[3] if len(cells) > 3 else "Active"

                            if not lic_num or lic_num in seen_licenses:
                                continue

                            seen_licenses.add(lic_num)
                            contractor_name, company_name = parse_utah_name(raw_name)

                            records.append({
                                "source_url": SEARCH_URL,
                                "contractor_name": contractor_name,
                                "company_name": company_name,
                                "license_number": lic_num,
                                "license_type": lic_type or request.license_type,
                                "license_status": status,
                                "expiration_date": "",
                                "address": "",
                                "city": request.city.title() if request.city else "Salt Lake City",
                                "state": "UT",
                                "zip_code": "",
                                "phone": ""
                            })
                except Exception as exc:
                    log(f"ZenRows query error for '{query}': {exc}", "warning")

        return records

    async def _try_scraperapi_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        lic_code = UT_LICENSE_TYPES.get(request.license_type, UT_LICENSE_TYPES["default"])
        search_query = "CONSTRUCTION"

        records = []
        try:
            params = {
                "api_key": SCRAPERAPI_KEY,
                "url": SEARCH_URL,
                "render": "true"
            }

            async with httpx.AsyncClient(timeout=60.0) as client:
                r1 = await client.get("http://api.scraperapi.com", params=params)
                if r1.status_code != 200:
                    return []

                soup1 = BeautifulSoup(r1.text, "html.parser")
                csrf_inp = soup1.find("input", {"name": "_csrf"})
                csrf_val = csrf_inp.get("value") if csrf_inp else ""

                form_data = {
                    "_csrf": csrf_val,
                    "action": "search",
                    "type": "by_name",
                    "startsWith": "false",
                    "fullName": search_query,
                    "professions": "207"
                }

                post_params = {
                    "api_key": SCRAPERAPI_KEY,
                    "url": SEARCH_URL
                }
                r2 = await client.post("http://api.scraperapi.com", params=post_params, data=form_data)
                
                soup = BeautifulSoup(r2.text, "html.parser")
                tables = soup.find_all("table")

                for table in tables:
                    rows = table.find_all("tr")
                    for row in rows:
                        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                        if len(cells) < 4 or "License" in cells[0] or "Name" in cells[0]:
                            continue

                        raw_name = cells[0]
                        lic_num = cells[1]
                        lic_type = cells[2]
                        status = cells[3] if len(cells) > 3 else "Active"

                        contractor_name, company_name = parse_utah_name(raw_name)
                        if not lic_num:
                            continue

                        records.append({
                            "source_url": SEARCH_URL,
                            "contractor_name": contractor_name,
                            "company_name": company_name,
                            "license_number": lic_num,
                            "license_type": lic_type or request.license_type,
                            "license_status": status,
                            "expiration_date": "",
                            "address": "",
                            "city": request.city.title() if request.city else "Salt Lake City",
                            "state": "UT",
                            "zip_code": "",
                            "phone": ""
                        })

                return records

        except Exception as exc:
            log(f"Utah ScraperAPI fallback failed: {exc}", "warning")
            return []
