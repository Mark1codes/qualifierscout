"""
LinkedIn enrichment service using DuckDuckGo site:linkedin.com search.
Runs after scraping to attempt to find LinkedIn profiles for each lead.
Uses concurrent batch processing for 5x faster enrichment.
"""
from __future__ import annotations

import asyncio
import time
from typing import Callable


def _clean_person_name(raw_name: str) -> str:
    """Extract First and Last name, removing middle initials/names."""
    parts = [p for p in raw_name.strip().split() if len(p) > 1 and not p.endswith(".")]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1]}"
    return raw_name


def _find_linkedin_sync(name: str, company: str, city: str, license_type: str) -> tuple[str, str]:
    """
    Synchronous search for a LinkedIn profile.
    Returns (discovered_person_name, linkedin_url).
    """
    try:
        from ddgs import DDGS
        trade = license_type.split("-")[0].strip() if "-" in license_type else license_type
        clean_trade = trade.replace("Contractor", "").replace("License", "").strip()
        
        if name:
            clean_name = _clean_person_name(name)
            query = f'site:linkedin.com/in/ {clean_name} {city} {clean_trade} contractor'
        else:
            query = f'site:linkedin.com/in/ "{company}" (owner OR president OR CEO OR founder OR manager OR director)'

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))

        for result in results:
            url = result.get("href", "")
            title = result.get("title", "")
            body = result.get("body", "").lower()
            
            if "linkedin.com/in/" in url:
                if name:
                    clean_name = _clean_person_name(name)
                    parts = [p.lower() for p in clean_name.split()]
                    title_lower = title.lower()
                    
                    if len(parts) >= 2:
                        name_match = (parts[0] in title_lower or parts[0] in body) and (parts[-1] in title_lower or parts[-1] in body)
                    else:
                        name_match = parts[0] in title_lower or parts[0] in body

                    if name_match:
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


async def _enrich_single_linkedin(record_index: int, record: dict, license_type: str) -> tuple[int, str, str]:
    """
    Enrich a single record with LinkedIn data. Runs in a thread to avoid blocking.
    Returns (record_index, discovered_name, linkedin_url).
    """
    name = record.get("contractor_name", "")
    company = record.get("company_name", "")
    city = record.get("city", "")

    try:
        discovered_name, linkedin_url = await asyncio.to_thread(
            _find_linkedin_sync, name, company, city, license_type
        )
        return record_index, discovered_name, linkedin_url
    except Exception:
        return record_index, "", ""


async def enrich_with_linkedin(
    records: list[dict],
    log: Callable,
    delay_seconds: float = 1.2,
    batch_size: int = 5,
) -> list[dict]:
    """
    For each record, perform a DuckDuckGo search to find LinkedIn profiles and
    discover decision-maker names for company-only leads.
    Uses concurrent batch processing for faster enrichment.
    """
    targets = [
        (i, r) for i, r in enumerate(records)
        if (r.get("contractor_name") or r.get("company_name")) 
        and not r.get("linkedin")
    ]

    if not targets:
        log("No leads to enrich with LinkedIn.", "info")
        return records

    log(f"Starting Ghost Hunter LinkedIn enrichment for {len(targets)} leads (batch size: {batch_size})...")

    found = 0
    total_batches = (len(targets) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start_idx = batch_num * batch_size
        end_idx = min(start_idx + batch_size, len(targets))
        batch = targets[start_idx:end_idx]

        # Fire all searches in this batch concurrently
        tasks = [
            _enrich_single_linkedin(
                record_index,
                record,
                record.get("license_type", "contractor")
            )
            for record_index, record in batch
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        for result in results:
            if isinstance(result, Exception):
                continue
            record_index, discovered_name, linkedin_url = result
            if linkedin_url:
                records[record_index]["linkedin"] = linkedin_url
                if discovered_name and not records[record_index].get("contractor_name"):
                    records[record_index]["contractor_name"] = discovered_name
                    company = records[record_index].get("company_name", "")
                    log(f"Ghost Hunter discovered decision maker for {company}: {discovered_name}")
                found += 1
                log(f"Found LinkedIn: {linkedin_url}")

        # Log progress every 2 batches
        if (batch_num + 1) % 2 == 0 or batch_num == total_batches - 1:
            processed = min(end_idx, len(targets))
            log(f"LinkedIn enrichment progress: {processed}/{len(targets)} leads processed ({found} profiles found so far).")

        # Rate limit pause between batches (not after the last batch)
        if batch_num < total_batches - 1:
            await asyncio.sleep(delay_seconds)

    log(f"LinkedIn enrichment complete: {found}/{len(targets)} profiles found.")
    return records
