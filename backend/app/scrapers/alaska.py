from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
import httpx

from app.schemas import ScrapeStartRequest

MAPSERVER_URL = "https://maps.commerce.alaska.gov/server/rest/services/Economics_Related/Business_Licenses/MapServer/2/query"
BASE_URL = "https://www.commerce.alaska.gov/cbp/main/search/professional"

AK_LICENSE_KEYWORDS = {
    "Underground Contractor": ["UNDERGROUND", "EXCAVAT", "UTILIT", "PIPELINE", "EARTHWORK", "TRENCHING", "SEWER", "WATER"],
    "General Contractor": ["CONTRACTOR", "CONSTRUCTION", "BUILD", "DEVELOPMENT"],
    "Building Contractor": ["BUILDING", "BUILD", "CONSTRUCTION", "HOME"],
    "Residential Contractor": ["RESIDENTIAL", "HOME", "HOUSING", "CUSTOM BUILD"],
    "Roofing Contractor": ["ROOFING", "ROOF"],
    "Electrical Contractor": ["ELECTRICAL", "ELECTRIC"],
    "HVAC Contractor": ["HVAC", "AIR CONDITIONING", "HEATING", "COOLING"],
    "Plumbing Contractor": ["PLUMBING", "PLUMBER"],
    "default": ["CONTRACTOR", "CONSTRUCTION", "UNDERGROUND"],
}

COMPANY_KEYWORDS = {
    "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC", "LLP", "LP",
    "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT", "DEVELOPMENTS",
    "ENTERPRISE", "ENTERPRISES", "PARTNER", "PARTNERS", "PROPERTY", "PROPERTIES", "CONTRACTING", "SOLUTION", "SOLUTIONS",
    "DESIGN", "DESIGNS", "ASSOCIATE", "ASSOCIATES", "VENTURE", "VENTURES", "HOLDING", "HOLDINGS", "INDUSTRY", "INDUSTRIES",
    "SYSTEM", "SYSTEMS", "CONTRACTOR", "CONTRACTORS", "REMODELING", "SHUTTER", "SHUTTERS", "WINDOW", "WINDOWS", "PAVING",
    "ENGINEERING", "MASONRY", "RENOVATION", "RENOVATIONS", "SERVICE", "SERVICES", "BUILD", "BUILDER", "BUILDERS", "GROUP",
    "AIR", "CONDITIONING", "UTILITY", "UTILITIES", "EXCAVATION", "EXCAVATING", "PIPELINE", "PIPELINES", "SUPPLY", "SUPPLIES",
    "RENTAL", "RENTALS", "EQUIPMENT", "TRUCKING", "HAULING", "TOWING", "AUTO", "AUTOMOTIVE", "REPAIR", "REPAIRS", "MECHANICAL",
    "COMMUNICATIONS", "COMMUNICATION", "TELECOM", "TELEPHONE", "CABLE", "WIRELESS", "BROADBAND", "NETWORK", "NETWORKS",
    "WATER", "WATERS", "WATERWAYS", "DRAIN", "SEWER", "SANITATION", "ENVIRONMENTAL", "CLEANING", "MAINTENANCE",
    "PETROLEUM", "OIL", "GAS", "ENERGY", "RESOURCE", "RESOURCES", "MINING", "EXPLORATION", "POWER", "SOLAR", "LOGISTICS",
    "COUNCIL", "BOARD", "DISTRICT", "AUTHORITY", "DEPT", "DEPARTMENT", "SOCIETY", "FOUNDATION", "TRUST", "CLUB", "ASSOC",
    "PRODUCTIONS", "PRODUCTION", "MEDIA", "STUDIO", "STUDIOS", "CREATIVE", "AGENCY", "CONSULTING", "CONSULTANTS", "ADVISORS",
    "SUPPORT", "METERING", "EARTHWORKS", "EARTHWORK", "RESTORATION", "REMOTE", "RIVER", "BELUGA", "DOCKS", "GUY", "BOYS", "BROS", "BROTHERS"
}


def parse_alaska_name(business_name: str, owners: str) -> tuple[str, str]:
    """
    Parses Alaska business name & owners field into (contractor_name, company_name).
    - Returns contractor_name only if owners contains a real human individual name.
    - Leaves contractor_name empty if owners is a corporate entity or company name.
    """
    company = business_name.strip()
    contractor = ""

    if not owners or not owners.strip():
        return "", company

    clean_owners = re.sub(r"[^\w\s;&,-]", " ", owners.upper()).strip()
    primary_owner = re.split(r"[;&]", clean_owners)[0].strip()
    words = [w for w in re.findall(r"\b[A-Z0-9]+\b", primary_owner) if len(w) > 1]

    # Check if primary owner contains any company keywords
    is_corporate = any(w in COMPANY_KEYWORDS for w in words)
    
    # Check if primary owner matches business name tokens
    clean_biz = set(re.findall(r"\b[A-Z0-9]+\b", business_name.upper()))
    overlap = set(words) & clean_biz
    if len(overlap) >= 2:
        is_corporate = True

    if not is_corporate and len(words) >= 2 and len(words) <= 4:
        if "," in owners:
            parts = [p.strip() for p in owners.split(",", 1)]
            last_part = parts[0]
            first_part = parts[1] if len(parts) > 1 else ""
            contractor = f"{first_part} {last_part}".strip().title()
        else:
            contractor = primary_owner.title()

    return contractor, company


class AlaskaScraper:
    name = "Alaska DCCED"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Querying State of Alaska DCCED REST API directly (100% Free, No anti-bot required)...")
        records = await self._fetch_alaska_records(request, log)
        if not records:
            log("Alaska DCCED query returned no records.", "warning")

        if request.individuals_only:
            records = [r for r in records if r.get("contractor_name")]

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_alaska_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _fetch_alaska_records(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            keywords = AK_LICENSE_KEYWORDS.get(request.license_type, AK_LICENSE_KEYWORDS["default"])
            
            # Construct SQL WHERE clause for ArcGIS MapServer
            where_conditions = ["Status = 'Active'"]

            if request.city and request.city.strip() and request.city.lower() != "all":
                city_clean = request.city.strip().upper()
                where_conditions.append(f"UPPER(PhysicalCity) LIKE '%{city_clean}%'")

            # Add keyword matching for trade/license type
            kw_clauses = [f"UPPER(BusinessName) LIKE '%{kw}%'" for kw in keywords]
            where_conditions.append(f"({' OR '.join(kw_clauses)})")

            where_clause = " AND ".join(where_conditions)
            log(f"Querying Alaska REST API for {request.license_type} ({request.city or 'All Cities'})...")

            params = {
                "where": where_clause,
                "outFields": "BusinessName,Owners,LicenseNumber,Status,PhysicalCity,PhysicalLine1,PhysicalState,PhysicalZipOut,ExpireDate",
                "resultRecordCount": min(request.max_records * 2, 500),
                "f": "json"
            }

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            }

            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(MAPSERVER_URL, params=params, headers=headers)
                if r.status_code != 200:
                    log(f"Alaska DCCED REST API status {r.status_code}", "warning")
                    return []

                data = r.json()
                features = data.get("features", [])
                log(f"Received {len(features)} matching records from Alaska DCCED REST API.")

                records = []
                seen_licenses = set()

                for feat in features:
                    attrs = feat.get("attributes", {})
                    lic_num = str(attrs.get("LicenseNumber") or "").strip()
                    biz_name = str(attrs.get("BusinessName") or "").strip()
                    owners = str(attrs.get("Owners") or "").strip()

                    if not lic_num or not biz_name:
                        continue

                    if lic_num in seen_licenses:
                        continue
                    seen_licenses.add(lic_num)

                    contractor_name, company_name = parse_alaska_name(biz_name, owners)

                    exp_ts = attrs.get("ExpireDate")
                    exp_date = ""
                    if exp_ts and isinstance(exp_ts, (int, float)):
                        try:
                            exp_date = datetime.fromtimestamp(exp_ts / 1000.0).strftime("%m/%d/%Y")
                        except Exception:
                            exp_date = ""

                    rec = {
                        "source_url": BASE_URL,
                        "license_type": request.license_type,
                        "contractor_name": contractor_name,
                        "company_name": company_name,
                        "license_number": lic_num,
                        "license_status": attrs.get("Status") or "Active",
                        "expiration_date": exp_date,
                        "address": attrs.get("PhysicalLine1") or "",
                        "city": (attrs.get("PhysicalCity") or request.city or "Anchorage").strip().title(),
                        "state": "AK",
                        "zip_code": attrs.get("PhysicalZipOut") or "",
                    }
                    records.append(rec)

                log(f"Successfully processed {len(records)} clean Alaska contractor records.")
                return records

        except Exception as exc:
            log(f"Alaska DCCED query failed: {exc}", "error")
            return []
