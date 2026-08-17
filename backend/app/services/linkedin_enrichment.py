"""
LinkedIn enrichment service using DuckDuckGo site:linkedin.com search.
Runs after scraping to attempt to find LinkedIn profiles for each lead.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable


def _find_linkedin_sync(name: str, city: str, license_type: str) -> str:
    """Synchronous DuckDuckGo search for a LinkedIn profile."""
    try:
        from ddgs import DDGS
        # Clean up license type for a punchy keyword search
        trade = license_type.split("-")[0].strip() if "-" in license_type else license_type
        
        # Hyper-strict query: Requires exact name match in quotes, plus the city and trade
        query = f'site:linkedin.com "{name}" {city} ({trade} OR construction)'
        
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
            
        parts = [p.lower() for p in name.split() if len(p) > 2]
        
        for result in results:
            url = result.get("href", "")
            title = result.get("title", "").lower()
            body = result.get("body", "").lower()
            
            if "linkedin.com/in/" in url:
                # STRICT VALIDATION 1: The person's FIRST and LAST name MUST be in the LinkedIn Title
                if len(parts) >= 2:
                    # Require both the first word (usually Last Name in NM) and second word (First Name)
                    name_match = (parts[0] in title) and (parts[1] in title)
                elif len(parts) == 1:
                    name_match = parts[0] in title
                else:
                    name_match = False
                
                # STRICT VALIDATION 2: The snippet must contain a relevant industry keyword
                trade_keywords = ["owner", "president", "construction", "contractor", "manager", "director", "infrastructure", "civil"]
                trade_match = any(kw in body or kw in title for kw in trade_keywords)
                
                if name_match and trade_match:
                    return url
    except Exception:
        pass
    return ""


async def enrich_with_linkedin(
    records: list[dict],
    log: Callable,
    delay_seconds: float = 1.5,
) -> list[dict]:
    """
    For each record that has a contractor_name (individual) and no linkedin,
    perform a DuckDuckGo search to find their LinkedIn profile URL.

    Uses asyncio.to_thread to run the blocking search off the event loop.
    Adds a polite delay between searches to avoid rate-limiting.

    Args:
        records: List of lead dicts from the scraper
        log: Log callback from the scrape run
        delay_seconds: Pause between each search request

    Returns:
        Same list with `linkedin` field populated where found
    """
    individuals = [
        (i, r) for i, r in enumerate(records)
        if (r.get("contractor_name") or r.get("company_name")) 
        and not r.get("linkedin")
        and r.get("state") == "NM"
    ]

    if not individuals:
        log("No individual names to enrich with LinkedIn.", "info")
        return records

    log(f"Starting LinkedIn enrichment for {len(individuals)} leads (this may take a moment)...")

    found = 0
    for idx, (record_index, record) in enumerate(individuals):
        name = record.get("contractor_name") or record.get("company_name")
        city = record.get("city", "")
        license_type = record.get("license_type", "contractor")

        try:
            linkedin_url = await asyncio.to_thread(
                _find_linkedin_sync, name, city, license_type
            )
            if linkedin_url:
                records[record_index]["linkedin"] = linkedin_url
                found += 1
                log(f"Found LinkedIn for {name}: {linkedin_url}")

        except Exception as e:
            # Non-fatal: just skip this lead's enrichment
            pass

        # Polite delay between searches
        if idx < len(individuals) - 1:
            await asyncio.sleep(delay_seconds)

    log(f"LinkedIn enrichment complete: {found}/{len(individuals)} profiles found.")
    return records
