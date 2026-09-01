from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup
from playwright.async_api import Page, async_playwright
from urllib.parse import urlencode, urlparse, parse_qs

from app.schemas import ScrapeStartRequest


OFFICIAL_SEARCH_URL = (
    "https://okcibv7prod.glsuite.us/GLSuiteWeb/Clients/OKCIB/Public/"
    "LicenseeSearch/LicenseeSearch.aspx"
)
OK_LICENSE_TYPES = {
    "Electrical Contractor": [
        "Electrical Contractor",
        "Electrical Contractor License",
        "Electrical",
    ],
    "Plumbing Contractor": [
        "Plumbing Contractor",
        "Plumbing Contractor License",
        "Plumbing",
    ],
    "Mechanical Contractor": [
        "Mechanical Contractor",
        "Mechanical Contractor License",
        "Mechanical",
    ],
    "HVAC Contractor": [
        "HVAC Contractor",
        "HVAC Contractor License",
        "Mechanical Contractor",
        "Mechanical Contractor License",
        "Mechanical",
    ],
    "Electrical Apprentice Registration": [
        "Electrical Apprentice Registration",
        "Electrical Apprentice",
    ],
    "Mechanical Apprentice Registration": [
        "Mechanical Apprentice Registration",
        "Mechanical Apprentice",
    ],
    "Electrical Journeyman License": [
        "Electrical Journeyman License",
        "Electrical Journeyman",
    ],
    "Mechanical Journeyman License": [
        "Mechanical Journeyman License",
        "Mechanical Journeyman",
    ],
    "Plumbing Journeyman License": [
        "Plumbing Journeyman License",
        "Plumbing Journeyman",
    ],
    "Plumbing Apprentice Registration": [
        "Plumbing Apprentice Registration",
        "Plumbing Apprentice",
    ],
}

OK_CITIES = {
    "OKLAHOMA CITY", "TULSA", "NORMAN", "BROKEN ARROW", "EDMOND", "LAWTON",
    "MOORE", "MIDWEST CITY", "ENID", "STILLWATER", "MUSKOGEE", "BARTLESVILLE",
    "OWASSO", "SHAWNEE", "ARDMORE", "YUKON", "BIXBY", "SAPULPA", "DUNCAN",
    "DEL CITY", "JENKS", "CLAREMORE", "MUSTANG", "SAND SPRINGS", "ADA",
    "PONCA CITY", "ALTUS", "EL RENO", "DURANT", "MIAMI", "MCALESTER",
    "CHICKASHA", "TAHLEQUAH", "WAGONER", "WOODWARD", "GUYMON", "WEATHERFORD",
}

CORP_INDICATORS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED",
    "LP", "LLP", "PLLC", "CONSTRUCTION", "PLUMBING", "HVAC", "MECHANICAL",
    "ELECTRIC", "ELECTRICAL", "CONTRACTING", "CONTRACTOR", "CONTRACTORS",
    "SERVICES", "SERVICE", "GROUP", "BUILDERS", "SYSTEMS", "AIR", "HEATING",
    "COOLING", "SHEET", "METAL", "INDUSTRIES", "ENTERPRISES", "SOLUTIONS",
}


def normalize_oklahoma_license_type(requested_type: str) -> str:
    normalized = clean_text(requested_type)
    if not normalized:
        raise ValueError("Oklahoma license type is required.")

    lowered = normalized.lower()
    for official_name in OK_LICENSE_TYPES:
        if lowered == official_name.lower():
            return official_name
        if any(lowered == alias.lower() for alias in OK_LICENSE_TYPES[official_name]):
            return official_name

    if "electrical" in lowered:
        return "Electrical Contractor"
    if "plumb" in lowered:
        return "Plumbing Contractor"
    if "mechanical" in lowered or "hvac" in lowered:
        return "Mechanical Contractor"
    raise ValueError(
        "Oklahoma CIB scraper supports Oklahoma contractor and apprentice license names only."
    )


def _looks_like_person_name(value: str) -> bool:
    name = clean_text(value)
    if not name:
        return False

    if "," in name:
        last_name, first_name = [part.strip() for part in name.split(",", 1)]
        if last_name and first_name:
            return True

    words = re.findall(r"[A-Za-z]+", name)
    return 2 <= len(words) <= 4 and not any(word.upper() in CORP_INDICATORS for word in words)


def parse_oklahoma_name(raw_name: str) -> tuple[str, str]:
    """
    Oklahoma licensee names can be either:
    - individual/person names like 'AARON, MARK LEE' or 'Mark Lee Aaron'
    - business names like 'ABC ELECTRIC LLC' or 'Smith Plumbing & Heating Co.'

    For this project, the target is usually the individual contractor when present,
    and the business name only when it is clearly an entity.
    """
    name = clean_text(raw_name)
    if not name:
        return "", ""

    words = set(re.findall(r"\b[A-Za-z0-9]+\b", name.upper()))
    is_company = bool(words & CORP_INDICATORS)

    if is_company:
        return "", name

    if _looks_like_person_name(name):
        if "," in name:
            last_name, first_name = [part.strip() for part in name.split(",", 1)]
            return f"{first_name.title()} {last_name.title()}".strip(), ""
        return name.title(), ""

    return "", name


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def pick_labeled_value(text: str, labels: list[str]) -> str:
    for label in labels:
        pattern = rf"{re.escape(label)}\s*:?\s*(.+?)(?=\s+[A-Z][A-Za-z /#-]{{2,}}\s*:|$)"
        match = re.search(pattern, text, re.I)
        if match:
            return clean_text(match.group(1))
    return ""


class OklahomaScraper:
    name = "Oklahoma CIB"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        license_type = normalize_oklahoma_license_type(request.license_type)
        records: list[dict] = []
        failures: list[str] = []

        log(f"Opening Oklahoma CIB license search directly for {license_type}...")
        try:
            records = await self._scrape_with_playwright(request, license_type, log)
        except Exception as exc:
            reason = self._safe_reason(exc)
            log(f"Oklahoma direct browser search failed: {reason}", "warning")
            failures.append(f"direct portal: {reason}")

        if not records:
            api_key = self._get_zenrows_api_key()
            if api_key:
                try:
                    log("Oklahoma ZenRows Scraping Browser fallback starting...", "info")
                    records = await self._fetch_with_scraping_browser(request, license_type, api_key, log)
                except Exception as exc:
                    reason = self._safe_reason(exc)
                    log(f"Oklahoma ZenRows fallback failed: {reason}", "warning")
                    failures.append(f"ZenRows API: {reason}")
            else:
                log("ZenRows API key not found. Skipping fallback.", "warning")

        if not records:
            detail = "; ".join(failures) or "no usable license records returned"
            raise RuntimeError(f"Oklahoma CIB scrape failed: {detail}")

        records = self._filter_records(records, request, license_type)
        records = records[: request.max_records]

        if not records:
            raise RuntimeError(
                "Oklahoma CIB scrape failed: the portal returned records but none matched the filters."
            )

        raw_path = self.raw_dir / f"run_{run_id}_oklahoma_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _scrape_with_playwright(self, request: ScrapeStartRequest, license_type: str, log) -> list[dict]:
        def _run_in_thread():
            import sys
            import asyncio
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(self._scrape_with_playwright_internal(request, license_type, log))

        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run_in_thread)
        except RuntimeError:
            return await self._scrape_with_playwright_internal(request, license_type, log)

    async def _scrape_with_playwright_internal(self, request: ScrapeStartRequest, license_type: str, log) -> list[dict]:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(**self._browser_launch_options())
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 900},
            )
            page = await context.new_page()
            try:
                response = await page.goto(OFFICIAL_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                status = response.status if response else 0
                if status in {401, 403, 429, 503}:
                    await page.wait_for_timeout(3_000)
                    text = await self._safe_body_text(page)
                    if self._is_blocked(text):
                        raise RuntimeError(f"official portal blocked browser access with status {status}")

                await self._run_portal_search(page, request, license_type, log)
                html = await page.content()
                records = self._parse_records(html, request, license_type)
                if not records:
                    raise RuntimeError("official portal rendered but returned no parseable records")
                log(f"Oklahoma CIB browser search extracted {len(records)} records.")
                return records
            finally:
                await browser.close()

    async def _run_portal_search(
        self,
        page: Page,
        request: ScrapeStartRequest,
        license_type: str,
        log,
    ) -> None:
        await page.wait_for_load_state("networkidle", timeout=45_000)
        body_text = await self._safe_body_text(page)
        if self._is_blocked(body_text):
            raise RuntimeError("Azure WAF bot check blocked the license form")

        city = clean_text(request.city)
        license_labels = OK_LICENSE_TYPES[license_type]

        await self._select_option_by_text(page, license_labels)
        if city:
            await self._fill_by_labels(page, ["city"], city)
        await self._fill_by_labels(page, ["state"], "OK")

        log(f"Submitting Oklahoma CIB search for {license_type} in {city or 'all cities'}...")
        submit = page.locator("#btnSubmit, input[type='submit'], input[type='button']").first
        if not await submit.count():
            submit = page.get_by_role("button", name=re.compile(r"search|find|submit", re.I)).first
        if not await submit.count():
            raise RuntimeError("could not find Oklahoma CIB search submit control")
        await submit.wait_for(state="visible", timeout=10_000)
        await submit.evaluate("element => element.click()")
        try:
            await page.wait_for_load_state("networkidle", timeout=45_000)
        except Exception:
            pass
        await page.wait_for_timeout(3_000)

    async def _select_option_by_text(self, page: Page, labels: list[str]) -> None:
        selects = page.locator("select")
        for index in range(await selects.count()):
            select = selects.nth(index)
            options = await select.locator("option").evaluate_all(
                "els => els.map(e => ({ value: e.value, text: e.innerText }))"
            )
            for option in options:
                option_text = clean_text(option.get("text"))
                if any(label.lower() in option_text.lower() for label in labels):
                    await select.select_option(value=option.get("value"), timeout=10_000)
                    return
        raise RuntimeError(f"could not find Oklahoma license type option matching {', '.join(labels)}")

    async def _fill_by_labels(self, page: Page, labels: list[str], value: str) -> None:
        for label in labels:
            locator = page.get_by_label(re.compile(label, re.I)).first
            if await locator.count():
                await locator.fill(value, timeout=5_000)
                return

        fields = page.locator("input[type='text'], input:not([type]), textarea")
        for index in range(await fields.count()):
            field = fields.nth(index)
            descriptor = " ".join(
                filter(
                    None,
                    [
                        await field.get_attribute("id"),
                        await field.get_attribute("name"),
                        await field.get_attribute("placeholder"),
                        await field.get_attribute("aria-label"),
                    ],
                )
            )
            if any(label.lower() in descriptor.lower() for label in labels):
                await field.fill(value, timeout=5_000)
                return

    def _parse_records(self, html: str, request: ScrapeStartRequest, license_type: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        records: list[dict] = []
        seen: set[str] = set()

        for row in soup.find_all("tr"):
            cells = [clean_text(cell.get_text(" ", strip=True)) for cell in row.find_all(["td", "th"])]
            if len(cells) < 2 or any("licensee search" in cell.lower() for cell in cells):
                continue
            record = self._record_from_cells(cells, request, license_type)
            if record and record["license_number"] not in seen:
                seen.add(record["license_number"])
                records.append(record)

        if records:
            return records

        text_blocks = [
            clean_text(tag.get_text(" ", strip=True))
            for tag in soup.find_all(["div", "li", "section"])
            if "license" in tag.get_text(" ", strip=True).lower()
        ]
        for text in text_blocks:
            record = self._record_from_text(text, request, license_type)
            if record and record["license_number"] not in seen:
                seen.add(record["license_number"])
                records.append(record)

        return records

    def _record_from_cells(self, cells: list[str], request: ScrapeStartRequest, license_type: str) -> dict | None:
        combined = " ".join(cells)
        license_number = self._extract_license_number(combined)
        if not license_number:
            return None

        status = self._extract_status(combined)
        expiration = self._extract_expiration(combined)
        phone = self._extract_phone(combined)
        city = self._extract_city(cells, request.city)
        address = self._extract_address(cells)
        raw_name = self._extract_name(cells, combined, license_number)
        contractor_name, company_name = parse_oklahoma_name(raw_name)

        returned_type = self._extract_license_type(combined, license_type)
        return {
            "source_url": OFFICIAL_SEARCH_URL,
            "contractor_name": contractor_name,
            "company_name": company_name,
            "license_number": license_number,
            "license_type": returned_type,
            "license_status": status,
            "expiration_date": expiration,
            "address": address,
            "city": city,
            "state": "OK",
            "zip_code": self._extract_zip(combined),
            "phone": phone,
        }

    def _record_from_text(self, text: str, request: ScrapeStartRequest, license_type: str) -> dict | None:
        license_number = self._extract_license_number(text)
        if not license_number:
            return None
        name = pick_labeled_value(text, ["Name", "Licensee", "Business Name", "Company"])
        contractor_name, company_name = parse_oklahoma_name(name)
        return {
            "source_url": OFFICIAL_SEARCH_URL,
            "contractor_name": contractor_name,
            "company_name": company_name,
            "license_number": license_number,
            "license_type": self._extract_license_type(text, license_type),
            "license_status": self._extract_status(text),
            "expiration_date": self._extract_expiration(text),
            "address": pick_labeled_value(text, ["Address", "Location"]),
            "city": self._extract_city([text], request.city),
            "state": "OK",
            "zip_code": self._extract_zip(text),
            "phone": self._extract_phone(text),
        }

    def _filter_records(self, records: list[dict], request: ScrapeStartRequest, license_type: str) -> list[dict]:
        filtered = []
        requested_status = clean_text(request.license_status).lower()
        for record in records:
            license_number = clean_text(record.get("license_number"))
            if not license_number:
                continue
            type_text = clean_text(record.get("license_type") or license_type).lower()
            if not any(label.lower() in type_text for label in OK_LICENSE_TYPES[license_type]):
                continue
            if requested_status and requested_status != "all":
                status = clean_text(record.get("license_status")).lower()
                if requested_status not in status:
                    continue
            if request.individuals_only and not record.get("contractor_name"):
                continue
            filtered.append(record)
        return filtered

    @staticmethod
    async def _safe_body_text(page: Page) -> str:
        try:
            return await page.locator("body").inner_text(timeout=5_000)
        except Exception:
            return ""

    @staticmethod
    def _is_blocked(text: str) -> bool:
        lowered = text.lower()
        return any(term in lowered for term in ["azure waf", "checking you're not a bot", "not a bot", "access denied"])

    @staticmethod
    def _extract_license_number(text: str) -> str:
        patterns = [
            r"(?:license|lic(?:ense)?\s*#?|number)\s*:?\s*([A-Z]{0,4}-?\d{4,}[A-Z0-9-]*)",
            r"\b([A-Z]{1,4}-?\d{5,}[A-Z0-9-]*)\b",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_status(text: str) -> str:
        """
        Extract license status from portal text.
        Returns the actual status value (Active, Expired, Suspended, Revoked, etc.)
        to match the portal's exact status representation.
        """
        text_upper = text.upper()
        
        # Check for specific status values in order of priority
        # These match the Oklahoma CIB portal's actual status labels
        status_patterns = [
            (r"\b(SUSPENDED)\b", "Suspended"),
            (r"\b(REVOKED)\b", "Revoked"),
            (r"\b(EXPIRED|EXPIRATION)\b", "Expired"),
            (r"\b(CANCELLED|WITHDRAWN|INACTIVE)\b", "Inactive"),
            (r"\b(ACTIVE|CURRENT|VALID)\b", "Active"),
        ]
        
        for pattern, status in status_patterns:
            if re.search(pattern, text_upper):
                return status
        
        # Default to Active if no status found
        return "Active"

    @staticmethod
    def _extract_expiration(text: str) -> str:
        match = re.search(r"\b(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\b", text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_phone(text: str) -> str:
        match = re.search(r"(\(\d{3}\)\s*\d{3}-\d{4}|\d{3}[-.\s]\d{3}[-.\s]\d{4})", text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_zip(text: str) -> str:
        match = re.search(r"\bOK\s+(\d{5}(?:-\d{4})?)\b", text, re.I)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_city(cells: list[str], requested_city: str | None) -> str:
        for cell in cells:
            upper = cell.upper()
            for city in OK_CITIES:
                if re.search(rf"\b{re.escape(city)}\b", upper):
                    return city.title()
        return clean_text(requested_city).title() if requested_city else ""

    @staticmethod
    def _extract_address(cells: list[str]) -> str:
        for cell in cells:
            if re.search(r"\b\d{1,6}\s+[A-Za-z0-9 .'-]+\s+(st|street|ave|avenue|rd|road|dr|drive|ln|lane|blvd|circle|ct|court|way)\b", cell, re.I):
                return cell
        return ""

    @staticmethod
    def _extract_name(cells: list[str], combined: str, license_number: str) -> str:
        labeled_name = pick_labeled_value(combined, ["Name", "Licensee", "Business Name", "Company"])
        if labeled_name:
            return labeled_name
        for cell in cells:
            if license_number in cell:
                continue
            if re.search(r"\b(active|inactive|expired|license|expiration|phone|address|city|state)\b", cell, re.I):
                continue
            if len(cell) >= 4:
                return cell
        return ""

    @staticmethod
    def _extract_license_type(text: str, fallback: str) -> str:
        for license_type, labels in OK_LICENSE_TYPES.items():
            if any(label.lower() in text.lower() for label in labels):
                return license_type
        return fallback

    @staticmethod
    def _browser_launch_options() -> dict[str, Any]:
        options: dict[str, Any] = {"headless": True}
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
                break
        return options

    @staticmethod
    def _safe_reason(exc: Exception) -> str:
        message = f"{type(exc).__name__}: {exc}"
        message = re.sub(r"(apikey|api_key|token|authorization)=?[^&\s]+", r"\1=[redacted]", message, flags=re.I)
        return message[:500]

    @staticmethod
    def _get_zenrows_api_key() -> str:
        configured_value = os.getenv("ZENROWS_API_KEY", "").strip()
        if configured_value.startswith(("ws://", "wss://", "http://", "https://")):
            return parse_qs(urlparse(configured_value).query).get("apikey", [""])[0].strip()
        return configured_value

    async def _fetch_with_scraping_browser(self, request: ScrapeStartRequest, license_type: str, connection_url_or_key: str, log) -> list[dict]:
        def _run_in_thread():
            import sys
            import asyncio
            if sys.platform == "win32":
                asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
            return asyncio.run(self._fetch_with_scraping_browser_internal(request, license_type, connection_url_or_key, log))
            
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(None, _run_in_thread)
        except RuntimeError:
            return await self._fetch_with_scraping_browser_internal(request, license_type, connection_url_or_key, log)

    async def _fetch_with_scraping_browser_internal(self, request: ScrapeStartRequest, license_type: str, connection_url_or_key: str, log) -> list[dict]:
        if connection_url_or_key.startswith(("wss://", "ws://")):
            connection_url = connection_url_or_key
        else:
            connection_url = "wss://browser.zenrows.com?" + urlencode(
                {"apikey": connection_url_or_key, "proxy_country": "us", "session_ttl": "10m"}
            )
            
        async with async_playwright() as playwright:
            browser = await playwright.chromium.connect_over_cdp(connection_url, timeout=60_000)
            context = browser.contexts[0] if browser.contexts else await browser.new_context()
            page = await context.new_page()
            
            try:
                await page.goto(OFFICIAL_SEARCH_URL, wait_until="domcontentloaded", timeout=60_000)
                await self._run_portal_search(page, request, license_type, log)
                html = await page.content()
                records = self._parse_records(html, request, license_type)
                if not records:
                    raise RuntimeError("ZenRows browser rendered but returned no parseable records")
                log(f"Oklahoma CIB ZenRows search extracted {len(records)} records.")
                return records
            finally:
                await page.close()
                await browser.close()
