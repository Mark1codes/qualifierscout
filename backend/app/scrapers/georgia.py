"""Georgia contractor-license scraper for the public GOALS portal."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from app.schemas import ScrapeStartRequest

PORTAL_URL = "https://goals.sos.ga.gov/GASOSOneStop/s/licensee-search"
ZENROWS_URL = "https://api.zenrows.com/v1/"
PROFESSION = "Residential & Commercial General Contractors"
MAX_ATTEMPTS = 2

LICENSE_TYPE_MAP = {
    "General Contractor": "General Contractor",
    "General Contractor - Restricted": "General Contractor - Restricted",
    "Residential-Basic Contractor": "Residential-Basic Contractor",
    "Residential-Light Commercial Contractor": "Residential-Light Commercial Contractor",
    "Residential Contractor": "Residential-Basic Contractor",
}

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
    "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
    "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
    "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
    "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
}


def parse_georgia_name(licensee_raw: str, company_raw: str = "") -> tuple[str, str]:
    """
    Parses Georgia GOALS name strings into (contractor_name, company_name).
    """
    licensee = (licensee_raw or "").strip()
    company = (company_raw or "").strip()

    if company and company.upper() != licensee.upper():
        if "," in licensee:
            parts = [p.strip() for p in licensee.split(",", 1)]
            contractor_name = f"{parts[1]} {parts[0]}".strip()
        else:
            contractor_name = licensee
        return contractor_name, company

    target = company or licensee
    if not target:
        return "", ""

    words = set(re.findall(r"\b[A-Za-z0-9]+\b", target.upper()))
    is_corporate = bool(words & CORP_INDICATORS)

    if "," in target:
        parts = [p.strip() for p in target.split(",", 1)]
        last_part = parts[0]
        first_part = parts[1] if len(parts) > 1 else ""

        if is_corporate:
            return "", target
        else:
            contractor_name = f"{first_part} {last_part}".strip()
            return contractor_name, target

    if is_corporate:
        return "", target
    else:
        return target, target


class GeorgiaScraper:
    name = "Georgia GOALS"

    def __init__(self, raw_data_dir: Path) -> None:
        self.raw_data_dir = raw_data_dir
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(
        self,
        request: ScrapeStartRequest,
        run_id: int,
        log: Callable[[str, str], None],
    ) -> list[dict]:
        license_type = LICENSE_TYPE_MAP.get(request.license_type, request.license_type)
        records: list[dict] = []
        failures: list[str] = []

        log(f"Starting Georgia GOALS scrape for '{request.license_type}'.")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                log(f"Georgia direct portal attempt {attempt}/{MAX_ATTEMPTS}.")
                records = await self._fetch_direct(request, license_type, log)
                if records:
                    log(f"Georgia direct portal returned {len(records)} usable records.")
                    break
                failures.append("direct portal returned no usable license records")
            except Exception as exc:
                reason = self._safe_error(exc)
                failures.append(f"direct portal: {reason}")
                log(f"Georgia direct portal attempt {attempt} failed: {reason}", "warning")

        if not records:
            # 1. Try ZenRows API / Scraping Browser
            api_key = self._get_zenrows_api_key()
            if api_key:
                try:
                    log("Georgia ZenRows API fallback starting...", "info")
                    records = await self._fetch_with_zenrows(request, license_type, api_key)
                    if not records:
                        log("Georgia ZenRows Scraping Browser starting...", "info")
                        records = await self._fetch_with_scraping_browser(request, license_type, api_key, log)
                    if records:
                        log(f"Georgia ZenRows returned {len(records)} usable records.", "info")
                except Exception as exc:
                    reason = self._safe_error(exc)
                    failures.append(f"ZenRows API: {reason}")
                    log(f"Georgia ZenRows fallback failed: {reason}", "warning")

            # 2. Try Zyte API Fallback
            if not records:
                zyte_key = os.getenv("ZYTE_API_KEY", "").strip()
                if zyte_key:
                    try:
                        log("Georgia Zyte API fallback starting...", "info")
                        records = await self._fetch_with_zyte(request, license_type, zyte_key, log)
                        if records:
                            log(f"Georgia Zyte API returned {len(records)} usable records.", "info")
                    except Exception as exc:
                        reason = self._safe_error(exc)
                        failures.append(f"Zyte API: {reason}")
                        log(f"Georgia Zyte API fallback failed: {reason}", "warning")

        records = self._finalize_records(records, request)
        if not records:
            detail = "; ".join(failures[-3:]) or "no usable license records returned"
            raise RuntimeError(f"Georgia scrape failed after direct and fallback paths: {detail}")

        records = records[: request.max_records]
        raw_path = self.raw_data_dir / f"run_{run_id}_georgia_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} Georgia raw records to {raw_path.name}.")
        return records

    async def _fetch_direct(self, request: ScrapeStartRequest, license_type: str, log: Callable[[str, str], None]) -> list[dict]:
        def _run_in_thread():
            import sys
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(self._fetch_direct_internal(request, license_type, log))

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run_in_thread)
        except RuntimeError:
            return await self._fetch_direct_internal(request, license_type, log)

    async def _fetch_direct_internal(self, request: ScrapeStartRequest, license_type: str, log: Callable[[str, str], None]) -> list[dict]:
        captured: list[dict[str, Any]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**self._browser_launch_options())
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 768},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = await context.new_page()
            await Stealth().apply_stealth_async(page)

            async def capture_response(response: Any) -> None:
                if "aura" not in response.url.lower() or response.status != 200:
                    return
                try:
                    # Ignore massive component JS bundle files to prevent memory exhaustion
                    if "auraCmpDef" in response.url or "auraFW" in response.url:
                        return
                    text = await response.text()
                    if "rows" in text or "licenseDataList" in text or "totalRows" in text:
                        clean = text.split("*/", 1)[-1] if "*/" in text else text
                        data = json.loads(clean)
                        for action in data.get("actions", []):
                            ret = action.get("returnValue")
                            if isinstance(ret, dict) and "rows" in ret:
                                rows = ret["rows"]
                                log(f"Captured {len(rows)} raw contractor records from Salesforce network payload.", "info")
                                captured.extend(rows)
                except Exception:
                    return

            page.on("response", capture_response)
            try:
                log("Navigating to Georgia GOALS portal...", "info")
                await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(3000)

                # Check for Cloudflare challenge
                body_text = (await page.locator("body").inner_text(timeout=15_000)).lower()
                if "just a moment" in body_text or "cf-chl" in body_text:
                    log("Cloudflare challenge detected, waiting 8s for auto-solve...", "info")
                    await page.wait_for_timeout(8000)
                    body_text = (await page.locator("body").inner_text()).lower()
                    if "just a moment" in body_text:
                        raise RuntimeError("Cloudflare challenge blocked request")

                # 1. Select Profession Type via UI
                prof_btn = page.locator('button[name="GASOS_Profession_Type__c"]')
                if await prof_btn.is_visible(timeout=10000):
                    await prof_btn.click()
                    await page.wait_for_timeout(1000)
                    prof_opt = page.locator('lightning-base-combobox-item:has-text("Residential & Commercial General Contractors")')
                    if await prof_opt.is_visible(timeout=5000):
                        await prof_opt.click()
                        await page.wait_for_timeout(1500)

                # 2. Select License Type via UI
                lic_btn = page.locator('button[name="GASOS_License_Type__c"]')
                if await lic_btn.is_visible(timeout=5000):
                    await lic_btn.click()
                    await page.wait_for_timeout(1000)
                    lic_opt = page.locator(f'lightning-base-combobox-item:has-text("{license_type}")').first
                    if not await lic_opt.is_visible(timeout=3000):
                        lic_opt = page.locator('lightning-base-combobox-item').first
                    if await lic_opt.is_visible():
                        await lic_opt.click()
                        await page.wait_for_timeout(1000)

                # 3. Fill search input (Last Name or Business Name)
                last_name_input = page.locator('input[name="lastName"]')
                if await last_name_input.is_visible():
                    await last_name_input.click()
                    search_term = request.city or "Smith"
                    await last_name_input.fill(search_term)
                    await page.wait_for_timeout(1000)

                # 4. Trigger Search Click
                search_button = page.locator('button:has-text("Search")').first
                if await search_button.is_visible():
                    await search_button.click()
                    log("Search button clicked via Playwright locator.", "info")

                await self._wait_for_results(page, captured)
                
                # Check shadow DOM state as secondary fallback
                shadow_rows = await page.evaluate("""() => {
                    const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
                    return searchCmp && searchCmp.licenseDataList ? searchCmp.licenseDataList : [];
                }""")
                if shadow_rows:
                    log(f"Extracted {len(shadow_rows)} records from searchCmp shadow DOM state.", "info")
                    captured.extend(shadow_rows)

                html = await page.content()
                captured.extend(self._extract_records_from_html(html))
            finally:
                await context.close()
                await browser.close()
        return self._deduplicate_candidates(captured)

    async def _wait_for_results(self, page: Any, captured: list[dict[str, Any]]) -> None:
        for _ in range(30):
            if captured:
                return
            body = (await page.locator("body").inner_text()).lower()
            if any(marker in body for marker in ("no records", "no results", "license number")):
                return
            await asyncio.sleep(1)
        raise RuntimeError("timed out waiting for Georgia search results")

    async def _fetch_with_zyte(
        self,
        request: ScrapeStartRequest,
        license_type: str,
        api_key: str,
        log: Callable[[str, str], None]
    ) -> list[dict]:
        search_term = request.city or "Smith"
        endpoint = "https://api.zyte.com/v1/extract"
        auth = (api_key, "")

        js_submit = f"""
        (() => {{
            function queryDeep(selector, root = document) {{
                let elements = Array.from(root.querySelectorAll(selector));
                const children = Array.from(root.querySelectorAll('*'));
                for (const child of children) {{
                    if (child.shadowRoot) elements = elements.concat(queryDeep(selector, child.shadowRoot));
                }}
                return elements;
            }}

            const interval = setInterval(() => {{
                const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
                if (searchCmp) {{
                    clearInterval(interval);
                    searchCmp.recaptchaIsactive = false;
                    searchCmp.isInputValid = () => true;
                    searchCmp.selectedProfessionType = 'Residential & Commercial General Contractors';
                    searchCmp.selectedLicenseType = {json.dumps(license_type)};
                    searchCmp.lastName = {json.dumps(search_term)};
                    searchCmp.showIndividualFields = true;
                    searchCmp.value = 'Individual';
                    searchCmp.pageIndex = 0;

                    const lastInps = queryDeep('input[name="lastName"]');
                    if (lastInps.length > 0) {{
                        lastInps[0].value = {json.dumps(search_term)};
                        lastInps[0].dispatchEvent(new Event('input', {{ bubbles: true, composed: true }}));
                        lastInps[0].dispatchEvent(new Event('change', {{ bubbles: true, composed: true }}));
                    }}

                    if (typeof searchCmp.handleSearchClick === 'function') {{
                        searchCmp.handleSearchClick();
                    }} else {{
                        const btns = queryDeep('button');
                        const searchBtn = btns.find(b => (b.innerText || '').trim().toLowerCase() === 'search');
                        if (searchBtn) searchBtn.click();
                    }}
                }}
            }}, 500);
        }})();
        """

        js_extract = """
        (() => {
            const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
            const debugInfo = {
                rows: searchCmp && searchCmp.licenseDataList ? searchCmp.licenseDataList : []
            };
            const container = document.createElement('script');
            container.id = 'scraped-contractors-payload';
            container.type = 'application/json';
            container.text = JSON.stringify(debugInfo);
            document.head.appendChild(container);
        })();
        """

        payload = {
            "url": PORTAL_URL,
            "browserHtml": True,
            "javascript": True,
            "actions": [
                {"action": "waitForTimeout", "timeout": 4.0},
                {"action": "evaluate", "source": js_submit},
                {"action": "waitForTimeout", "timeout": 8.0},
                {"action": "evaluate", "source": js_extract}
            ]
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(endpoint, auth=auth, json=payload)
            if resp.status_code != 200:
                if log:
                    log(f"Zyte API Error ({resp.status_code}): {resp.text[:300]}", "warning")
                resp.raise_for_status()
            html = resp.json().get("browserHtml", "")
            return self._extract_records_from_html(html)

    async def _fetch_with_scrapingant(
        self,
        request: ScrapeStartRequest,
        license_type: str,
        api_key: str,
        log: Callable[[str, str], None]
    ) -> list[dict]:
        search_term = request.city or "Smith"
        js_code = f"""
        (() => {{
            function queryDeep(selector, root = document) {{
                let elements = Array.from(root.querySelectorAll(selector));
                const children = Array.from(root.querySelectorAll('*'));
                for (const child of children) {{
                    if (child.shadowRoot) elements = elements.concat(queryDeep(selector, child.shadowRoot));
                }}
                return elements;
            }}

            setTimeout(() => {{
                const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
                if (searchCmp) {{
                    searchCmp.recaptchaIsactive = false;
                    searchCmp.selectedProfessionType = 'Residential & Commercial General Contractors';
                    searchCmp.selectedLicenseType = {json.dumps(license_type)};
                    searchCmp.lastName = {json.dumps(search_term)};
                    if (typeof searchCmp.handleSearchClick === 'function') {{
                        searchCmp.handleSearchClick();
                    }}
                }}
            }}, 3500);

            setTimeout(() => {{
                const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
                const debugInfo = {{
                    rows: searchCmp && searchCmp.licenseDataList ? searchCmp.licenseDataList : []
                }};
                const container = document.createElement('script');
                container.id = 'scraped-contractors-payload';
                container.type = 'application/json';
                container.text = JSON.stringify(debugInfo);
                document.head.appendChild(container);
            }}, 9500);
        }})()
        """

        b64_js = base64.b64encode(js_code.encode()).decode()
        endpoint = "https://api.scrapingant.com/v2/general"
        params = {
            "url": PORTAL_URL,
            "x-api-key": api_key,
            "browser": "true",
            "js_snippet": b64_js,
            "proxy_type": "datacenter"
        }

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.get(endpoint, params=params)
            resp.raise_for_status()
            return self._extract_records_from_html(resp.text)

    async def _fetch_with_zenrows(self, request: ScrapeStartRequest, license_type: str, api_key: str) -> list[dict]:
        search_term = request.city.strip() if request.city else "Smith"
        js_submit = f"""
        (() => {{
            function queryDeep(selector, root = document) {{
                let elements = Array.from(root.querySelectorAll(selector));
                const children = Array.from(root.querySelectorAll('*'));
                for (const child of children) {{
                    if (child.shadowRoot) {{
                        elements = elements.concat(queryDeep(selector, child.shadowRoot));
                    }}
                }}
                return elements;
            }}

            const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
            if (!searchCmp) return;

            searchCmp.recaptchaIsactive = false;
            searchCmp.isInputValid = () => true;

            searchCmp.selectedProfessionType = "Residential & Commercial General Contractors";
            searchCmp.selectedLicenseType = {json.dumps(license_type)};
            searchCmp.lastName = {json.dumps(search_term)};
            searchCmp.showIndividualFields = true;
            searchCmp.value = "Individual";
            searchCmp.pageIndex = 0;

            const lastInps = queryDeep('input[name="lastName"]');
            if (lastInps.length > 0) {{
                lastInps[0].value = {json.dumps(search_term)};
                lastInps[0].dispatchEvent(new Event('input', {{ bubbles: true, composed: true }}));
                lastInps[0].dispatchEvent(new Event('change', {{ bubbles: true, composed: true }}));
            }}

            if (typeof searchCmp.handleSearchClick === 'function') {{
                searchCmp.handleSearchClick();
            }} else {{
                const btns = queryDeep('button');
                const searchBtn = btns.find(b => (b.innerText || '').trim().toLowerCase() === 'search');
                if (searchBtn) searchBtn.click();
            }}
        }})();
        """

        js_extract = """
        (() => {
            const searchCmp = document.querySelector('c-gasos-professional-licensee-search');
            const debugInfo = {
                rows: searchCmp && searchCmp.licenseDataList ? searchCmp.licenseDataList : []
            };

            const container = document.createElement('script');
            container.id = 'scraped-contractors-payload';
            container.type = 'application/json';
            container.text = JSON.stringify(debugInfo);
            document.head.appendChild(container);
        })();
        """

        instructions = [
            {"wait": 4000},
            {"evaluate": js_submit},
            {"wait": 8000},
            {"evaluate": js_extract},
        ]
        params = {
            "apikey": api_key,
            "url": PORTAL_URL,
            "js_render": "true",
            "premium_proxy": "true",
            "js_instructions": json.dumps(instructions),
        }
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            response = await client.get(ZENROWS_URL, params=params)
            response.raise_for_status()
        return self._extract_records_from_html(response.text)

    async def _fetch_with_scraping_browser(
        self,
        request: ScrapeStartRequest,
        license_type: str,
        connection_url_or_key: str,
        log: Callable[[str, str], None] = None
    ) -> list[dict]:
        def _run_in_thread():
            import sys
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(self._fetch_with_scraping_browser_internal(request, license_type, connection_url_or_key, log))

        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run_in_thread)
        except RuntimeError:
            return await self._fetch_with_scraping_browser_internal(request, license_type, connection_url_or_key, log)

    async def _fetch_with_scraping_browser_internal(
        self,
        request: ScrapeStartRequest,
        license_type: str,
        connection_url_or_key: str,
        log: Callable[[str, str], None] = None
    ) -> list[dict]:
        if connection_url_or_key.startswith(("wss://", "ws://")):
            connection_url = connection_url_or_key
        else:
            connection_url = "wss://browser.zenrows.com?" + urlencode(
                {"apikey": connection_url_or_key, "proxy_country": "us", "session_ttl": "2m"}
            )
        captured: list[dict[str, Any]] = []
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(connection_url, timeout=60_000)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()

            async def capture_response(response: Any) -> None:
                if "aura" not in response.url.lower() or response.status != 200:
                    return
                try:
                    if "auraCmpDef" in response.url or "auraFW" in response.url:
                        return
                    text = await response.text()
                    if "rows" in text or "licenseDataList" in text or "totalRows" in text:
                        clean = text.split("*/", 1)[-1] if "*/" in text else text
                        data = json.loads(clean)
                        for action in data.get("actions", []):
                            ret = action.get("returnValue")
                            if isinstance(ret, dict) and "rows" in ret:
                                rows = ret["rows"]
                                if log:
                                    log(f"Captured {len(rows)} raw contractor records via Remote Scraping Browser.", "info")
                                captured.extend(rows)
                except Exception:
                    return

            page.on("response", capture_response)
            try:
                await page.goto(PORTAL_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.wait_for_timeout(3000)

                # Form interaction
                prof_btn = page.locator('button[name="GASOS_Profession_Type__c"]')
                await prof_btn.click()
                await page.wait_for_timeout(1000)
                await page.locator('lightning-base-combobox-item:has-text("Residential & Commercial General Contractors")').click()
                await page.wait_for_timeout(2500)

                lic_btn = page.locator('button[name="GASOS_License_Type__c"]')
                await lic_btn.click()
                await page.wait_for_timeout(1000)
                await page.locator('lightning-base-combobox-item:has-text("Commercial General Contractor Qualifying Agent")').first.click()
                await page.wait_for_timeout(1500)

                last_name_input = page.locator('input[name="lastName"]')
                await last_name_input.click()
                await last_name_input.fill(request.city or "Smith")
                await page.wait_for_timeout(1000)

                search_btn = page.locator('button:has-text("Search")').first
                await search_btn.click()
                await page.wait_for_timeout(8000)

                await self._wait_for_results(page, captured)
                captured.extend(self._extract_records_from_html(await page.content()))
            finally:
                await page.close()
                await browser.close()
        return self._deduplicate_candidates(captured)

    @staticmethod
    def _browser_launch_options() -> dict[str, Any]:
        options: dict[str, Any] = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-web-security",
                "--disable-site-isolation-trials",
                "--no-sandbox"
            ]
        }
        if sys.platform != "win32":
            return options

        chrome_paths = (
            Path(os.environ.get("PROGRAMFILES", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe",
        )
        for chrome_path in chrome_paths:
            if chrome_path.is_file():
                options["executable_path"] = str(chrome_path)
                return options
        return options

    @staticmethod
    def _get_zenrows_api_key() -> str:
        configured_value = os.getenv("ZENROWS_API_KEY", "").strip()
        if configured_value.startswith(("ws://", "wss://", "http://", "https://")):
            return parse_qs(urlparse(configured_value).query).get("apikey", [""])[0].strip()
        return configured_value

    @staticmethod
    def _click_option_script(label: str) -> str:
        return (
            "const option = [...document.querySelectorAll('[role=option], lightning-base-combobox-item, span, div')].find((item) => "
            f"(item.innerText || item.textContent || '').trim().toLowerCase().includes({json.dumps(label.lower())}));"
            "if (option) option.click();"
        )

    @staticmethod
    def _click_search_script() -> str:
        return (
            "const button = [...document.querySelectorAll('button')].find((item) => "
            "(item.innerText || item.textContent || '').trim().toLowerCase() === 'search');"
            "if (button) button.click();"
        )

    def _extract_records_from_json(self, payload: str | bytes) -> list[dict[str, Any]]:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8", errors="replace")
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        return self._extract_mappings(data)

    def _extract_records_from_html(self, html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict[str, Any]] = []

        payload_tag = soup.find("script", id="scraped-contractors-payload")
        if payload_tag and payload_tag.string:
            records.extend(self._extract_records_from_json(payload_tag.string.strip()))

        for script in soup.find_all("script"):
            if not script.string:
                continue
            records.extend(self._extract_records_from_json(script.string.strip()))

        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            headers = [self._normalise_header(cell.get_text(" ", strip=True)) for cell in rows[0].find_all(["th", "td"])]
            if not any("license" in header for header in headers):
                continue
            for row in rows[1:]:
                cells = [cell.get_text(" ", strip=True) for cell in row.find_all("td")]
                if not cells:
                    continue
                records.extend(self._extract_mappings([dict(zip(headers, cells))]))
        return self._deduplicate_candidates(records)

    @staticmethod
    def _normalise_header(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")

    def _extract_mappings(self, value: Any) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if isinstance(value, dict):
            if self._license_value(value):
                records.append(value)
            for child in value.values():
                records.extend(self._extract_mappings(child))
        elif isinstance(value, list):
            for child in value:
                records.extend(self._extract_mappings(child))
        return records

    def _finalize_records(self, candidates: Iterable[dict[str, Any]], request: ScrapeStartRequest) -> list[dict]:
        records = []
        for candidate in self._deduplicate_candidates(candidates):
            record = self._normalise_record(candidate, request)
            if not record["license_number"]:
                continue
            if request.license_status and request.license_status.lower() not in record["license_status"].lower():
                continue
            records.append(record)
        return self._deduplicate_records(records)

    def _normalise_record(self, candidate: dict[str, Any], request: ScrapeStartRequest) -> dict:
        name = self._value(candidate, "licenseeName", "licensee_name", "licensee", "name", "business_name", "businessname")
        raw_company = self._value(candidate, "companyName", "company_name", "business_name", "businessname")
        contractor_name, company_name = parse_georgia_name(name, raw_company)

        return {
            "source_url": PORTAL_URL,
            "state": "GA",
            "contractor_name": contractor_name,
            "company_name": company_name,
            "license_number": self._license_value(candidate),
            "license_type": self._value(candidate, "licenseType", "license_type", "license_class", "classification") or request.license_type,
            "license_status": self._value(candidate, "status", "licenseStatus", "license_status", "license_status_description") or "Active",
            "city": self._value(candidate, "city", "licenseCity", "license_city", "business_city") or request.city or "",
            "address": self._value(candidate, "address", "street_address", "business_address"),
            "zip_code": self._value(candidate, "zip", "zipCode", "zip_code", "postal_code"),
            "phone": self._value(candidate, "phone", "phoneNumber", "phone_number"),
            "email": "",
            "linkedin": "",
        }

    @staticmethod
    def _value(candidate: dict[str, Any], *keys: str) -> str:
        normalised = {re.sub(r"[^a-z0-9]", "", str(key).lower()): value for key, value in candidate.items()}
        for key in keys:
            value = normalised.get(re.sub(r"[^a-z0-9]", "", key.lower()))
            if value is not None:
                return str(value).strip()
        return ""

    def _license_value(self, candidate: dict[str, Any]) -> str:
        return self._value(candidate, "licenseNumber", "license_number", "license", "licenseId", "license_id")

    @staticmethod
    def _looks_like_business(value: str) -> bool:
        return any(marker in value.upper() for marker in ("LLC", "INC", "CORP", "LTD", "CO.", "COMPANY", "GROUP", "SERVICES", "CONSTRUCTION", "&"))

    def _deduplicate_candidates(self, candidates: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        records = []
        for candidate in candidates:
            license_number = self._license_value(candidate)
            if not license_number or license_number in seen:
                continue
            seen.add(license_number)
            records.append(candidate)
        return records

    @staticmethod
    def _deduplicate_records(records: Iterable[dict]) -> list[dict]:
        seen: set[str] = set()
        unique = []
        for record in records:
            license_number = record["license_number"]
            if license_number in seen:
                continue
            seen.add(license_number)
            unique.append(record)
        return unique

    @staticmethod
    def _safe_error(exc: Exception) -> str:
        message = str(exc).replace("\n", " ").strip()
        message = re.sub(r"(?:apikey|api_key|token|authorization)=?[^&\s]+", "[redacted]", message, flags=re.I)
        return message.encode("ascii", "backslashreplace").decode("ascii")[:240] or type(exc).__name__
