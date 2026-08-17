from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from app.schemas import ScrapeStartRequest


SOURCE_URL = "https://portal.nclbgc.org/Public/Search"


class NorthCarolinaScraper:
    name = "North Carolina NCLBGC"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Opening North Carolina license search.")
        records = await self._try_public_search(request, log)
        if not records:
            log("Live source returned no parseable rows.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_north_carolina_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                search_data = {}
                if request.city:
                    search_data["City"] = request.city
                
                if not search_data:
                    log("No search parameters provided (e.g., City). Search might fail or return too many results.", "warning")

                log("Submitting search request...")
                response = await client.post("https://portal.nclbgc.org/Public/_Search/", data=search_data)
                response.raise_for_status()

                soup = BeautifulSoup(response.text, "html.parser")
                rows = soup.select("#AccountSearchTable tbody tr")
                log(f"Found {len(rows)} matching accounts on the search page.")

                records = []
                for row in rows[:request.max_records]:
                    a_tag = row.find("a")
                    if not a_tag or "ShowAccountDetails" not in a_tag.get("onclick", ""):
                        continue
                    
                    onclick_text = a_tag["onclick"]
                    match = re.search(r"ShowAccountDetails\(\s*'([^']+)'", onclick_text)
                    if not match:
                        continue
                    
                    account_id = match.group(1)
                    detail_url = f"https://portal.nclbgc.org/Public/_ShowAccountDetails/?key={account_id}&Source=Search"
                    
                    try:
                        detail_resp = await client.get(detail_url)
                        detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                        
                        record = {
                            "source_url": SOURCE_URL,
                            "license_type": request.license_type,
                        }

                        def get_field(label):
                            label_div = detail_soup.find("div", class_="display-label", string=re.compile(label, re.I))
                            if label_div:
                                field_div = label_div.find_next_sibling("div", class_="display-field")
                                if field_div:
                                    return field_div.get_text(separator="\n").strip()
                            return ""

                        name_val = get_field("Name")
                        # NC portal packs all name info into one field with newlines.
                        # Format examples:
                        #   "Ronald S. Smith, T/A\nAKA: Highland Construction\nRonald S. Smith"
                        #   "1st Choice Construction Services, LLC"  (just a business, no individual)
                        # Strategy: Extract AKA line as company_name, last line as individual name.
                        contractor_name = name_val
                        company_name = ""
                        if name_val:
                            name_lines = [l.strip() for l in name_val.split("\n") if l.strip()]
                            aka_lines = [l for l in name_lines if l.upper().startswith("AKA:")]
                            non_aka_lines = [l for l in name_lines if not l.upper().startswith("AKA:")]
                            
                            if aka_lines:
                                # Has an explicit AKA business name line
                                company_name = aka_lines[0].replace("AKA:", "").replace("AKA :", "").strip()
                                # The individual's real name is usually the last non-AKA line
                                contractor_name = non_aka_lines[-1] if non_aka_lines else name_lines[0]
                            elif len(non_aka_lines) == 1:
                                # Single name — could be a business LLC or a solo contractor
                                # If it ends with LLC/Inc/Corp it's a company, otherwise it's a person
                                name_upper = non_aka_lines[0].upper()
                                if any(suffix in name_upper for suffix in ["LLC", "INC", "CORP", "CO.", "LTD", "COMPANY", "CONSTRUCTION", "SERVICES", "BUILDERS", "GROUP"]):
                                    company_name = non_aka_lines[0]
                                    contractor_name = ""  # No individual name available
                                else:
                                    contractor_name = non_aka_lines[0]
                            else:
                                # Multiple lines but no AKA — first line is likely the business, last is the person
                                company_name = non_aka_lines[0] if len(non_aka_lines) > 1 else ""
                                contractor_name = non_aka_lines[-1]
                                
                        record["contractor_name"] = contractor_name
                        record["company_name"] = company_name
                        address = get_field("Address")
                        record["phone"] = get_field("Phone")
                        record["license_number"] = get_field("License #")
                        record["expiration_date"] = get_field("Expiration Date")
                        record["license_status"] = "Active" if "Not Active" not in row.text else "Inactive"
                        
                        if address:
                            parts = [p.strip() for p in address.split('\n') if p.strip()]
                            if len(parts) >= 2:
                                record["address"] = " ".join(parts[:-1])
                                last_line = parts[-1]
                                city_state_zip_match = re.match(r"(.*?),\s*([A-Z]{2})\s+([\d-]+)", last_line)
                                if city_state_zip_match:
                                    record["city"] = city_state_zip_match.group(1).strip()
                                    record["state"] = city_state_zip_match.group(2).strip()
                                    record["zip_code"] = city_state_zip_match.group(3).strip()
                                else:
                                    record["city"] = last_line
                            else:
                                record["address"] = address
                                
                        if not record.get("state"):
                            record["state"] = "NC"

                        records.append(record)
                    except Exception as e:
                        log(f"Error fetching details for {account_id}: {e}", "warning")
                
                return records
        except Exception as exc:
            log(f"Could not fetch live NCLBGC search page: {exc}", "warning")
            return []

