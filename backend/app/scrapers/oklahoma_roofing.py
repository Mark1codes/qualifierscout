"""
Oklahoma CIB Roofing Contractor Scraper
========================================
Queries https://verify.cib.hbesystems.com/api/roofing/search
Fast, direct REST API extraction with 0 browser overhead.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from app.schemas import ScrapeStartRequest

API_SEARCH_URL = "https://verify.cib.hbesystems.com/api/roofing/search"


class OklahomaRoofingScraper:
    name = "Oklahoma CIB Roofing"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log(f"Querying Oklahoma Roofing REST API for {request.license_type} in {request.city or 'Statewide'}...")
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "application/json"
        }

        params: dict[str, Any] = {
            "limit": min(max(request.max_records * 3, 100), 1000),
            "offset": 0,
            "where": "{}"
        }

        records: list[dict] = []
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            resp = await client.get(API_SEARCH_URL, headers=headers, params=params)
            if resp.status_code != 200:
                log(f"Oklahoma Roofing API error: status {resp.status_code}", "error")
                return []

            raw_data = resp.json()
            log(f"Retrieved {len(raw_data)} raw records from Oklahoma Roofing API.")

            req_city = (request.city or "").strip().upper()
            req_status = (request.license_status or "").strip().lower()

            for item in raw_data:
                status_name = item.get("statusName") or "Active"
                if req_status and req_status not in ["all", "any"]:
                    if "in good standing" not in status_name.lower():
                        continue

                comp_city = (item.get("company_city") or "").strip().upper()
                if req_city and req_city not in ["ALL", "ANY", "STATEWIDE"] and req_city not in comp_city:
                    continue

                first = (item.get("person_firstName") or "").strip()
                last = (item.get("person_lastName") or "").strip()
                person_name = f"{first} {last}".strip().title()
                comp_name = (item.get("company_name") or "").strip().title()

                status = "Active" if "in good standing" in status_name.lower() else status_name

                rec = {
                    "source_url": "https://verify.cib.hbesystems.com/roofing/search",
                    "contractor_name": person_name,
                    "company_name": comp_name,
                    "license_number": str(item.get("licenseNumber") or ""),
                    "license_type": "Roofing Contractor",
                    "license_status": status,
                    "address": (item.get("company_address1") or "").strip().title(),
                    "city": comp_city.title(),
                    "state": "OK",
                    "zip_code": (item.get("company_zipcode") or "").strip(),
                    "phone": (item.get("company_telephone") or "").strip(),
                }
                
                if request.individuals_only and not rec["contractor_name"]:
                    continue
                    
                records.append(rec)

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_oklahoma_roofing_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} Oklahoma roofing records to {raw_path.name}.")
        return records
