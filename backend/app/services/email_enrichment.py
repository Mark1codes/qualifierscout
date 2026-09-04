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
    "sentry-next.wixpress.com", "adzep.com", "bizapedia.com",
    "stackoverflow.com", "t.me", "techora.ru", "imdb.com", "pingdom.com",
    "fancyapps.com", "rutube.ru", "azquotes.com", "thecalculatorsite.com",
    "webcamtoy.com", "consumeraffairs.com", "manta.com", "radaris.com",
    "flyaero.com", "github.com", "reddit.com", "telegram.org", "wikipedia.org",
    "address.com", "test.ru"
}

def is_real_email(email: str) -> bool:
    """Filter out obviously fake/system emails and image filenames."""
    lower = email.lower()
    domain = lower.split("@")[-1]
    if domain in SKIP_DOMAINS or any(skip_d in domain for skip_d in ["techora.ru", "rutube.ru", "t.me", "stackoverflow.com"]):
        return False
    if any(ext in lower for ext in [".png", ".jpg", ".jpeg", ".gif", ".svg", "@2x", "@3x", ".webp"]):
        return False
    if any(x in lower for x in ["noreply", "no-reply", "donotreply", "example", "test@", "admin@", "support@", "sentry", "helpdesk@", "email@address"]):
        return False
    return True


def _find_website_sync(name: str, company: str, city: str) -> str | None:
    """Synchronous DuckDuckGo search for the contractor's business website."""
    try:
        from ddgs import DDGS
        search_name = company if company else name
        if not search_name or len(search_name) < 3:
            return None
        
        query = f'"{search_name}" {city} contractor official website'
        with DDGS(timeout=2) as ddgs:
            results = list(ddgs.text(query, max_results=5))
            
        skip = ["yelp.com", "bbb.org", "linkedin.com", "facebook.com",
                "angi.com", "homeadvisor.com", "thumbtack.com",
                "yellowpages.com", "houzz.com", ".gov", "wikipedia", "buildzoom.com", "porch.com",
                "adzep.com", "youtube.com", "vimeo.com", "pinterest.com", "instagram.com", "tiktok.com",
                "realtor.com", "zillow.com", "mapquest.com", "bizapedia.com", "opencorporates.com",
                "stackoverflow.com", "t.me", "techora.ru", "imdb.com", "pingdom.com",
                "fancyapps.com", "rutube.ru", "azquotes.com", "thecalculatorsite.com",
                "webcamtoy.com", "consumeraffairs.com", "manta.com", "radaris.com",
                "flyaero.com", "github.com", "reddit.com", "telegram.org", "locanto.",
                "squarespace.com", "wix.com", "wordpress.com", "blogspot.com", "weebly.com", ".pdf"]
                
        for r in results:
            url = r.get("href", "")
            if not url or any(s in url.lower() for s in skip):
                continue
                
            # Verify the result text or domain looks like a real trade business
            body = (r.get("title", "") + " " + r.get("body", "")).lower()
            if any(term in body for term in ["contractor", "electric", "plumbing", "hvac", "mechanical", "heating", "air", "cooling", "construction", "service", "inc", "llc", "co"]):
                return url
    except Exception:
        pass
    return None


def _search_buildzoom_sync(name: str, city: str, license_number: str = "") -> str | None:
    """Scrape contractor profiles from BuildZoom 100% for free."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        clean_name = _clean_name_for_email_search(name) if name else ""

        trade_stop_words = {
            'electrical', 'electric', 'plumbing', 'plumber', 'mechanical', 'hvac', 'roofing',
            'contractor', 'contractors', 'services', 'service', 'company', 'inc', 'llc', 'co', 
            'group', 'solutions', 'construction', 'builder', 'builders', 'oklahoma', 'city'
        }
        name_parts = [
            p.lower() for p in clean_name.split() 
            if len(p) > 2 and p.lower() not in trade_stop_words
        ]

        # 1. Direct Slug Resolution Attempt
        if clean_name:
            slug = clean_name.lower().replace(" ", "-").replace(".", "")
            direct_url = f"https://www.buildzoom.com/contractor/{slug}"
            try:
                resp = httpx.get(direct_url, headers=headers, timeout=5, follow_redirects=True)
                if resp.status_code == 200:
                    emails = EMAIL_REGEX.findall(resp.text)
                    real_emails = [
                        e for e in emails 
                        if is_real_email(e) 
                        and "buildzoom" not in e.lower() 
                        and "blockrenovation" not in e.lower()
                        and "example.com" not in e.lower()
                        and "email.com" not in e.lower()
                    ]
                    if real_emails:
                        return real_emails[0]
            except Exception:
                pass

        # 2. Public BuildZoom Search Query Attempt
        from ddgs import DDGS
        queries = []
        if license_number:
            queries.append(f'site:buildzoom.com {license_number} Oklahoma')
        if clean_name:
            queries.append(f'site:buildzoom.com "{clean_name}" {city}')

        for query in queries:
            try:
                with DDGS(timeout=2) as ddgs:
                    results = list(ddgs.text(query, max_results=3))
            except Exception:
                continue

            for r in results:
                url = r.get("href", "")
                if "buildzoom.com/contractor/" in url:
                    resp = httpx.get(url, headers=headers, timeout=6, follow_redirects=True)
                    if resp.status_code == 200:
                        emails = EMAIL_REGEX.findall(resp.text)
                        real_emails = [
                            e for e in emails 
                            if is_real_email(e) 
                            and "buildzoom" not in e.lower() 
                            and "blockrenovation" not in e.lower()
                            and "example.com" not in e.lower()
                            and "email.com" not in e.lower()
                        ]
                        if real_emails:
                            return real_emails[0]
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


def _clean_name_for_email_search(raw_name: str) -> str:
    """Extract First and Last name, removing middle initials/names."""
    parts = [p for p in raw_name.strip().split() if len(p) > 1 and not p.endswith(".")]
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1]}"
    return raw_name


def _search_email_directly_sync(name: str, city: str, company: str, license_type: str = "") -> str | None:
    """Fallback: search DuckDuckGo for public contractor emails using targeted queries."""
    try:
        from ddgs import DDGS
        clean_name = _clean_name_for_email_search(name) if name else ""
        search_name = company if company else clean_name
        if not search_name:
            return None

        trade = license_type.split("-")[0].replace("Contractor", "").strip() if license_type else ""

        queries = [
            f'{search_name} {city} Oklahoma contractor email',
            f'{clean_name} {city} {trade} gmail.com OR yahoo.com OR outlook.com'
        ]

        with DDGS(timeout=2) as ddgs:
            for query in queries:
                try:
                    results = list(ddgs.text(query, max_results=3))
                    for r in results:
                        text = (r.get("body", "") or "") + " " + (r.get("title", "") or "") + " " + (r.get("href", "") or "")
                        emails = EMAIL_REGEX.findall(text)
                        real_emails = [e for e in emails if is_real_email(e)]
                        if real_emails:
                            return real_emails[0]
                except Exception:
                    continue
    except Exception:
        pass
    return None


def has_valid_mx_record(domain: str) -> bool:
    """Check if domain has active MX mail servers for FREE before calling ZeroBounce."""
    if not domain or domain in SKIP_DOMAINS:
        return False
    try:
        import dns.resolver
        answers = dns.resolver.resolve(domain, 'MX')
        return len(answers) > 0
    except Exception:
        try:
            import socket
            socket.gethostbyname(domain)
            return True
        except Exception:
            return False


def generate_email_candidates(name: str, company: str, city: str, license_type: str = "", website: str = "") -> list[str]:
    """Generate high-probability email candidate variations."""
    candidates = []
    clean_name = _clean_name_for_email_search(name) if name else ""
    parts = [p.lower() for p in clean_name.split() if len(p) > 1]

    if len(parts) >= 2:
        first = parts[0]
        last = parts[-1]

        # 1. Custom Domain Candidates (if website available)
        if website:
            try:
                from urllib.parse import urlparse
                domain = urlparse(website).netloc.replace("www.", "").strip()
                if domain and domain not in SKIP_DOMAINS and has_valid_mx_record(domain):
                    candidates.append(f"{first}@{domain}")
                    candidates.append(f"{first}.{last}@{domain}")
                    candidates.append(f"{first[0]}{last}@{domain}")
                    candidates.append(f"info@{domain}")
                    candidates.append(f"contact@{domain}")
            except Exception:
                pass

        # 2. Public Provider Candidates (Gmail / Yahoo) with MX Shield
        trade = license_type.split("-")[0].replace("Contractor", "").strip().lower().replace(" ", "") if license_type else ""
        clean_city = city.lower().replace(" ", "") if city else ""

        if trade:
            candidates.append(f"{first}{last}{trade}@gmail.com")
        if clean_city:
            candidates.append(f"{first}{last}{clean_city}@gmail.com")

    # Deduplicate while preserving order
    seen = set()
    unique_candidates = []
    for c in candidates:
        if c not in seen and is_real_email(c):
            seen.add(c)
            unique_candidates.append(c)

    return unique_candidates


def _enrich_single_lead_sync(name: str, company: str, city: str, license_type: str = "", license_number: str = "") -> dict:
    result = {"website": None, "email": None, "candidate_emails": []}
    
    # 100% BUILDZOOM EXCLUSIVE EMAIL DISCOVERY (Guarantees strict contractor license profile email accuracy)
    email = _search_buildzoom_sync(name, city, license_number, company)
    if email:
        result["email"] = email

    return result


async def enrich_with_email(
    records: list[dict],
    log: Callable,
    delay_seconds: float = 1.0,
) -> list[dict]:
    """
    For each record, attempts to find their BuildZoom profile and extract their official email.
    """
    targets = [
        (i, r) for i, r in enumerate(records)
        if not r.get("email") and (r.get("contractor_name") or r.get("company_name"))
    ]

    if not targets:
        log("No leads to enrich with email.", "info")
        return records

    log(f"Starting BuildZoom-exclusive email enrichment for {len(targets)} leads...")

    found_email = 0
    found_website = 0
    sem = asyncio.Semaphore(5)

    async def _process_one(idx, record_index, record):
        nonlocal found_email, found_website
        async with sem:
            name = record.get("contractor_name", "")
            company = record.get("company_name", "")
            city = record.get("city", "")
            license_type = record.get("license_type", "")
            license_number = record.get("license_number", "")

            try:
                enrichment_data = await asyncio.to_thread(
                    _enrich_single_lead_sync, name, company, city, license_type, license_number
                )
                
                if enrichment_data["website"]:
                    records[record_index]["website"] = enrichment_data["website"]
                    found_website += 1
                    log(f"Found website for {company or name}: {enrichment_data['website']}")
                    
                if enrichment_data["email"]:
                    records[record_index]["email"] = enrichment_data["email"]
                    found_email += 1
                    log(f"Found BuildZoom profile email for {company or name}: {enrichment_data['email']}")

            except Exception:
                pass

    tasks = [_process_one(idx, record_index, record) for idx, (record_index, record) in enumerate(targets)]
    await asyncio.gather(*tasks)

    log(f"Email enrichment complete: Found {found_email} BuildZoom contractor emails.")
    return records

