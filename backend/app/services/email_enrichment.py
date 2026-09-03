"""
Email enrichment service using DuckDuckGo and website scraping.
Attempts to find a contractor's business website and scrape their contact email.
"""
from __future__ import annotations

import asyncio
import re
from typing import Callable

import httpx

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

SKIP_DOMAINS = {
    "sentry.io", "wixpress.com", "example.com", "schema.org",
    "w3.org", "googleapis.com", "jquery.com", "facebook.com",
    "twitter.com", "instagram.com", "youtube.com", "linkedin.com",
    "myfloridalicense.com", "nclbgc.org", "tdlr.texas.gov",
    "therealdeal.com", "wix.com", "squarespace.com", "wordpress.com",
    "sentry-next.wixpress.com", "adzep.com", "bizapedia.com"
}

def is_real_email(email: str) -> bool:
    """Filter out obviously fake/system emails."""
    lower = email.lower()
    domain = lower.split("@")[-1]
    if domain in SKIP_DOMAINS:
        return False
    if any(x in lower for x in ["noreply", "no-reply", "donotreply", "example", "test@", "admin@", "support@", "sentry"]):
        return False
    return True


def _find_website_sync(name: str, company: str, city: str) -> str | None:
    """Synchronous DuckDuckGo search for the contractor's business website."""
    try:
        from ddgs import DDGS
        search_name = company if company else name
        if not search_name:
            return None
        # Remove strict quotes which cause zero results on DDG
        query = f'{search_name} {city} contractor website'
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        for r in results:
            url = r.get("href", "")
            skip = ["yelp.com", "bbb.org", "linkedin.com", "facebook.com",
                    "angi.com", "homeadvisor.com", "thumbtack.com",
                    "yellowpages.com", "houzz.com", ".gov", "wikipedia", "buildzoom.com", "porch.com",
                    "adzep.com", "youtube.com", "vimeo.com", "pinterest.com", "instagram.com", "tiktok.com",
                    "realtor.com", "zillow.com", "mapquest.com", "bizapedia.com", "opencorporates.com"]
            if url and not any(s in url.lower() for s in skip):
                return url
    except Exception:
        pass
    return None


def _scrape_email_sync(website_url: str) -> str | None:
    """Scrape website for an email address."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    base = website_url.rstrip("/")
    pages_to_try = [
        base,
        f"{base}/contact",
        f"{base}/contact-us",
        f"{base}/about",
        f"{base}/about-us",
    ]

    for page_url in pages_to_try:
        try:
            r = httpx.get(page_url, headers=headers, timeout=5, follow_redirects=True)
            if r.status_code != 200:
                continue
            emails = EMAIL_REGEX.findall(r.text)
            real_emails = [e for e in emails if is_real_email(e)]
            if real_emails:
                return real_emails[0]
        except Exception:
            continue
    return None


def _search_email_directly_sync(name: str, city: str, company: str) -> str | None:
    """Fallback: search DuckDuckGo for the email directly."""
    try:
        from ddgs import DDGS
        search_name = company if company else name
        # Remove strict quotes
        query = f'{search_name} {city} contractor email'
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=3))
        for r in results:
            text = r.get("body", "") + " " + r.get("href", "")
            emails = EMAIL_REGEX.findall(text)
            real_emails = [e for e in emails if is_real_email(e)]
            if real_emails:
                return real_emails[0]
    except Exception:
        pass
    return None


def _enrich_single_lead_sync(name: str, company: str, city: str) -> dict:
    result = {"website": None, "email": None}
    
    website = _find_website_sync(name, company, city)
    if website:
        result["website"] = website
        email = _scrape_email_sync(website)
        if email:
            result["email"] = email
            return result
            
    email = _search_email_directly_sync(name, city, company)
    if email:
        result["email"] = email
        
    return result


async def enrich_with_email(
    records: list[dict],
    log: Callable,
    delay_seconds: float = 1.0,
) -> list[dict]:
    """
    For each record, attempts to find their website and scrape their email.
    """
    # Try to enrich any record that doesn't have an email yet
    targets = [
        (i, r) for i, r in enumerate(records)
        if not r.get("email") and (r.get("contractor_name") or r.get("company_name"))
    ]

    if not targets:
        log("No leads to enrich with email.", "info")
        return records

    log(f"Starting free email enrichment for {len(targets)} leads...")

    found_email = 0
    found_website = 0
    
    for idx, (record_index, record) in enumerate(targets):
        name = record.get("contractor_name", "")
        company = record.get("company_name", "")
        city = record.get("city", "")

        try:
            enrichment_data = await asyncio.to_thread(
                _enrich_single_lead_sync, name, company, city
            )
            
            if enrichment_data["website"]:
                records[record_index]["website"] = enrichment_data["website"]
                found_website += 1
                log(f"Found website for {company or name}: {enrichment_data['website']}")
                
            if enrichment_data["email"]:
                records[record_index]["email"] = enrichment_data["email"]
                found_email += 1
                log(f"Found email for {company or name}: {enrichment_data['email']}")

        except Exception:
            pass

        if idx < len(targets) - 1:
            await asyncio.sleep(delay_seconds)

    log(f"Email enrichment complete: Found {found_email} emails and {found_website} websites.")
    return records
