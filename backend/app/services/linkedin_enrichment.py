"""
LinkedIn enrichment service using DuckDuckGo site:linkedin.com search.
Runs after scraping to attempt to find LinkedIn profiles for each lead.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable


def _find_linkedin_sync(name: str, company: str, city: str, license_type: str) -> tuple[str, str]:
    """
    Synchronous search for a LinkedIn profile.
    Returns (discovered_person_name, linkedin_url).
    """
    try:
        from ddgs import DDGS
        trade = license_type.split("-")[0].strip() if "-" in license_type else license_type
        
        if name:
            query = f'site:linkedin.com "{name}" {city} ({trade} OR construction)'
        else:
            query = f'site:linkedin.com/in/ "{company}" (owner OR president OR CEO OR founder OR manager OR director)'

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))

        for result in results:
            url = result.get("href", "")
            title = result.get("title", "")
            body = result.get("body", "").lower()
            
            if "linkedin.com/in/" in url:
                if name:
                    parts = [p.lower() for p in name.split() if len(p) > 2]
                    title_lower = title.lower()
                    if len(parts) >= 2:
                        name_match = (parts[0] in title_lower) and (parts[1] in title_lower)
                    elif len(parts) == 1:
                        name_match = parts[0] in title_lower
                    else:
                        name_match = False

                    trade_keywords = ["owner", "president", "construction", "contractor", "manager", "director", "infrastructure", "civil"]
                    trade_match = any(kw in body or kw in title_lower for kw in trade_keywords)
                    
                    if name_match and trade_match:
                        return name, url
                else:
                    # Discover person's name from title: 'Steven Reynolds - Shearwater Communications | LinkedIn'
                    if "-" in title:
                        person_name = title.split("-")[0].replace("...", "").strip()
                        if len(person_name.split()) >= 2 and len(person_name.split()) <= 4:
                            return person_name, url
    except Exception:
        pass
    return "", ""


async def enrich_with_linkedin(
    records: list[dict],
    log: Callable,
    delay_seconds: float = 1.2,
) -> list[dict]:
    """
    For each record, perform a DuckDuckGo search to find LinkedIn profiles and
    discover decision-maker names for company-only leads.
    """
    targets = [
        (i, r) for i, r in enumerate(records)
        if (r.get("contractor_name") or r.get("company_name")) 
        and not r.get("linkedin")
    ]

    if not targets:
        log("No leads to enrich with LinkedIn.", "info")
        return records

    log(f"Starting Ghost Hunter LinkedIn enrichment for {len(targets)} leads...")

    found = 0
    for idx, (record_index, record) in enumerate(targets):
        name = record.get("contractor_name", "")
        company = record.get("company_name", "")
        city = record.get("city", "")
        license_type = record.get("license_type", "contractor")

        try:
            discovered_name, linkedin_url = await asyncio.to_thread(
                _find_linkedin_sync, name, company, city, license_type
            )
            if linkedin_url:
                records[record_index]["linkedin"] = linkedin_url
                if discovered_name and not records[record_index].get("contractor_name"):
                    records[record_index]["contractor_name"] = discovered_name
                    log(f"Ghost Hunter discovered decision maker for {company}: {discovered_name}")
                found += 1
                log(f"Found LinkedIn: {linkedin_url}")

        except Exception:
            pass

        if idx < len(targets) - 1:
            await asyncio.sleep(delay_seconds)

    log(f"LinkedIn enrichment complete: {found}/{len(targets)} profiles found.")
    return records
