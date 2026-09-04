"""
ZeroBounce Email Deliverability Verification Service.
Validates email addresses discovered via Apollo or Web Enrichment using ZeroBounce API v2.
Uses concurrent batch processing for 5x faster verification.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Dict, List

import httpx

ZEROBOUNCE_API_URL = "https://api.zerobounce.net/v2/validate"


async def _verify_single_email(
    client: httpx.AsyncClient,
    api_key: str,
    email: str,
    cache: Dict[str, Dict[str, Any]],
    log: Callable,
) -> tuple[str, str, Dict[str, Any] | None]:
    """
    Verify a single email with ZeroBounce.
    Returns (email, verification_status, cache_data).
    """
    # Check cache first
    if email in cache:
        data = cache[email]
        status = data.get("status", "")
        if status == "valid":
            return email, "Verified", None
        elif status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
            return email, "Invalid", None
        elif status in ("catch-all", "catch_all", "unknown"):
            return email, "Catch-All", None
        else:
            return email, status.capitalize() or "Unverified", None

    try:
        params = {
            "api_key": api_key,
            "email": email,
        }
        response = await client.get(ZEROBOUNCE_API_URL, params=params)

        if response.status_code == 200:
            data = response.json()
            status = (data.get("status") or "").lower().strip()
            sub_status = data.get("sub_status") or ""

            if status == "valid":
                log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' is VALID (1 ZeroBounce Credit Used).", "info")
                return email, "Verified", data
            elif status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
                log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' is {status.upper()} ({sub_status}) (1 ZeroBounce Credit Used). Stripped email to prevent bounces.", "warning")
                return email, "Invalid", data
            elif status in ("catch-all", "catch_all", "unknown"):
                log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' status is {status.upper()} (1 ZeroBounce Credit Used). Marked as Catch-All.", "info")
                return email, "Catch-All", data
            else:
                log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' returned '{status}' (1 ZeroBounce Credit Used).", "info")
                return email, status.capitalize() or "Unverified", data
        else:
            log(f"[ZEROBOUNCE ERROR] API returned status {response.status_code} for '{email}'.", "warning")
            return email, "Unverified", None

    except Exception as exc:
        log(f"[ZEROBOUNCE ERROR] Verification error for '{email}': {exc}", "warning")
        return email, "Unverified", None


async def verify_emails_with_zerobounce(
    records: List[Dict[str, Any]],
    log: Callable[[str, str], None],
    batch_size: int = 5,
    run_id: int | None = None,
) -> List[Dict[str, Any]]:
    """
    Validates emails in records using ZeroBounce API.
    Updates `verification_status` and removes invalid/spam-trap emails.
    Uses concurrent batch processing for faster verification.
    """
    api_key = os.getenv("ZEROBOUNCE_API_KEY", "").strip()
    if not api_key:
        log("ZeroBounce API key not configured in .env. Skipping email deliverability verification.", "info")
        return records

    target_indices = [i for i, r in enumerate(records) if r.get("email", "").strip()]
    if not target_indices:
        log("No emails found to verify with ZeroBounce.", "info")
        return records

    log(f"Starting ZeroBounce email deliverability verification for {len(target_indices)} emails (batch size: {batch_size})...")

    valid_count = 0
    invalid_count = 0
    zb_credits_used = 0
    cache: Dict[str, Dict[str, Any]] = {}

    total_batches = (len(target_indices) + batch_size - 1) // batch_size

    async with httpx.AsyncClient(timeout=15.0) as client:
        for batch_num in range(total_batches):
            start_idx = batch_num * batch_size
            end_idx = min(start_idx + batch_size, len(target_indices))
            batch_indices = target_indices[start_idx:end_idx]

            # Fire all verifications in this batch concurrently
            tasks = [
                _verify_single_email(
                    client, api_key, records[idx].get("email", "").strip(), cache, log
                )
                for idx in batch_indices
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Process results
            for idx, result in zip(batch_indices, results):
                if isinstance(result, Exception):
                    continue

                email, verification_status, cache_data = result

                # Update cache if we got new data
                if cache_data is not None:
                    cache[email] = cache_data
                    zb_credits_used += 1
                    from app.db.database import log_api_credit
                    log_api_credit("zerobounce", 1, run_id=run_id, details=f"ZeroBounce email check for {email}")


                # Apply result to record
                records[idx]["verification_status"] = verification_status
                if verification_status == "Verified":
                    valid_count += 1
                else:
                    # Clear non-valid emails (Catch-All, Invalid, etc.) so ONLY strictly ZeroBounce Valid emails remain
                    records[idx]["email"] = ""
                    invalid_count += 1

            # Log progress every 2 batches
            if (batch_num + 1) % 2 == 0 or batch_num == total_batches - 1:
                processed = min(end_idx, len(target_indices))
                log(f"ZeroBounce progress: {processed}/{len(target_indices)} emails verified. ({valid_count} Valid, {invalid_count} Invalid so far).")

            # Small pause between batches (not after last batch)
            if batch_num < total_batches - 1:
                await asyncio.sleep(0.2)

    log(f"[CREDIT SUMMARY] ZeroBounce verification completed: Used {zb_credits_used} ZeroBounce Credits for this run ({valid_count} Valid, {invalid_count} Invalid/Stripped).")
    return records
