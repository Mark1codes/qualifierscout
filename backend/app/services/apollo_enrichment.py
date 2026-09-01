import asyncio
import os
import httpx
from typing import List, Dict, Any


async def _enrich_single_apollo(
    client: httpx.AsyncClient,
    record: dict,
    url: str,
    api_key: str,
    log,
    run_id: int | None = None,
) -> tuple[dict, int, bool]:
    """
    Enrich a single record with Apollo data.
    Returns (updated_record, credits_used, should_drop).
    """
    name = record.get("contractor_name", "").strip()
    if not name:
        return record, 0, False

    parts = name.split(" ")
    first_name = parts[0]
    last_name = " ".join(parts[1:]) if len(parts) > 1 else ""

    if record.get("state") == "NM" and len(parts) > 1:
        first_name, last_name = last_name, first_name

    has_linkedin = bool(record.get("linkedin"))
    has_company = bool(record.get("company_name"))
    
    state_code = (record.get("state") or "").strip().upper()
    native_phone_states = {"AZ", "CA", "FL"}
    
    payload = {
        "first_name": first_name,
        "last_name": last_name,
        "reveal_personal_emails": True,
        "reveal_phone_number": False  # GUARANTEE: Never spend 8 Mobile Credits per lead for phone reveals
    }
    
    # Determine search mode
    search_mode = "standard"
    if has_linkedin:
        payload["linkedin_url"] = record.get("linkedin")
        search_mode = "linkedin"
    elif has_company:
        payload["organization_name"] = record.get("company_name")
        search_mode = "company"
    else:
        # Fallback: Individual contractor with no company or LinkedIn
        # Add geographic context (city/state) for more precise matching
        city = (record.get("city") or "").strip()
        state = (record.get("state") or "").strip()
        if city or state:
            if city:
                payload["city"] = city
            if state:
                payload["state"] = state
            search_mode = "individual_fallback"
            log(f"[APOLLO FALLBACK] Searching individual contractor '{name}' with geographic context ({city}, {state})...")
        else:
            # No triangulation point available at all
            log(f"[APOLLO CREDIT GUARD] Skipping Apollo for '{name}' (No LinkedIn, Company Name, or Location). Saved 1 Apollo credit.")
            return record, 0, False

    headers = {
        "X-Api-Key": api_key,
        "Content-Type": "application/json"
    }

    credits_used = 0
    should_drop = False

    try:
        log(f"[APOLLO LOG] Searching Apollo database for: {name}... (Mode: {search_mode})")
        from app.db.database import log_api_credit
        from app.services.network_guard import safe_http_request
        log_api_credit("apollo_search", 1, run_id=run_id, details=f"Apollo {search_mode} search for {name}")

        response = await safe_http_request(client, "POST", url, log=log, json=payload, headers=headers)

        if response and response.status_code == 200:
            data = response.json()
            person = data.get("person", {})

            if person:
                # Apollo billed 1 credit for returning this matched person profile
                credits_used = 1
                log_api_credit("apollo", 1, run_id=run_id, details=f"Apollo {search_mode} match for {name}")

                # Extract Email (Strictly Verified Only)
                email = person.get("email") or person.get("personal_email")
                email_status = (person.get("email_status") or "").lower()

                if email:
                    if email_status == "verified" or email_status == "extrapolated":
                        record["email"] = email
                        log(f"[APOLLO CREDIT LOG] Unlocked verified email for '{name}': {email} (1 Apollo Credit Used).")
                    else:
                        log(f"[APOLLO LOG] Apollo matched '{name}' (1 Credit Used), but email status was '{email_status}'. Ignored email to prevent bounces.")
                else:
                    log(f"[APOLLO LOG] Apollo matched '{name}' profile details (1 Credit Used).")


                # Extract Phone
                phone = person.get("phone_numbers", [])
                if phone and len(phone) > 0:
                    record["phone"] = phone[0].get("sanitized_number") or phone[0].get("raw_number")

                # Extract LinkedIn
                linkedin = person.get("linkedin_url")
                if linkedin:
                    record["linkedin"] = linkedin

                # Extract Title
                title = person.get("title")
                if not title:
                    emp_history = person.get("employment_history")
                    if emp_history and isinstance(emp_history, list) and len(emp_history) > 0:
                        title = emp_history[0].get("title")

                if title:
                    record["title"] = title

                # Extract Company Name (if missing from raw scrape)
                company_name_found = ""
                if not record.get("company_name"):
                    org = person.get("organization")
                    if org and org.get("name"):
                        company_name_found = org.get("name")
                        if org.get("website_url") and not record.get("website"):
                            record["website"] = org.get("website_url")
                    else:
                        emp_history = person.get("employment_history")
                        if emp_history and isinstance(emp_history, list) and len(emp_history) > 0:
                            company_name_found = emp_history[0].get("organization_name")

                if company_name_found:
                    clean_name = company_name_found.lower().strip()
                    if clean_name not in ["self employed", "self-employed", "freelance", "freelancer", "independent contractor"]:
                        record["company_name"] = company_name_found

                # Blacklist check to ensure we don't return government agencies or retail
                bad_company_keywords = [
                    "city of", "county", "state of", "department", "public schools",
                    "university", "beauty", "salon", "hospital", "gov", "police", "sheriff", "federal"
                ]

                current_company = record.get("company_name", "").lower()
                if current_company and any(kw in current_company for kw in bad_company_keywords):
                    log(f"[APOLLO LOG] Apollo found blacklisted company '{record.get('company_name')}'. Tagging lead for removal.")
                    should_drop = True

            else:
                log(f"[APOLLO LOG] Apollo found no match for {name}.")
        elif response:
            log(f"[APOLLO ERROR] Apollo API Error {response.status_code}: {response.text}", "error")
    except Exception as e:
        log(f"[APOLLO ERROR] Apollo Request Failed: {e}", "error")

    return record, credits_used, should_drop


async def enrich_with_apollo(
    records: List[Dict[str, Any]],
    log,
    batch_size: int = 3,
    run_id: int | None = None,
) -> List[Dict[str, Any]]:
    api_key = os.getenv("APOLLO_API_KEY", "").strip()
    if not api_key:
        log("No Apollo API key found in .env. Skipping premium enrichment.", "warning")
        return records

    url = "https://api.apollo.io/api/v1/people/match"
    apollo_credits_used = 0
    drop_indices = set()

    total_batches = (len(records) + batch_size - 1) // batch_size

    async with httpx.AsyncClient(timeout=30) as client:
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(records))
            batch = [(i, records[i]) for i in range(start_idx, end_idx)]

            # Fire all Apollo calls in this batch concurrently
            tasks = [
                _enrich_single_apollo(client, record, url, api_key, log, run_id=run_id)
                for _, record in batch
            ]

            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for (original_idx, _), result in zip(batch, results):
                if isinstance(result, Exception):
                    log(f"[APOLLO ERROR] Batch error: {result}", "error")
                    continue
                updated_record, credits, should_drop = result
                records[original_idx] = updated_record
                apollo_credits_used += credits
                if should_drop:
                    drop_indices.add(original_idx)

            # Log progress every 3 batches
            if (batch_num + 1) % 3 == 0 or batch_num == total_batches - 1:
                processed = min(end_idx, len(records))
                log(f"Apollo enrichment progress: {processed}/{len(records)} leads processed. Credits used so far: {apollo_credits_used}.")

            # Small pause between batches to respect rate limits (not after last batch)
            if batch_num < total_batches - 1:
                await asyncio.sleep(0.3)

    # Filter out the leads we tagged for removal
    valid_records = [r for i, r in enumerate(records) if i not in drop_indices]
    dropped_count = len(drop_indices)
    if dropped_count > 0:
        log(f"[APOLLO LOG] Apollo Enrichment dropped {dropped_count} leads due to blacklisted company names.")

    log(f"[CREDIT SUMMARY] Apollo Enrichment Finished: Used {apollo_credits_used} Apollo Credits for this run.")
    return valid_records
