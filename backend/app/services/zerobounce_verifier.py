"""
ZeroBounce Email Deliverability Verification Service.
Validates email addresses discovered via Apollo or Web Enrichment using ZeroBounce API v2.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Dict, List

import httpx

ZEROBOUNCE_API_URL = "https://api.zerobounce.net/v2/validate"


async def verify_emails_with_zerobounce(
    records: List[Dict[str, Any]],
    log: Callable[[str, str], None]
) -> List[Dict[str, Any]]:
    """
    Validates emails in records using ZeroBounce API.
    Updates `verification_status` and removes invalid/spam-trap emails.
    """
    api_key = os.getenv("ZEROBOUNCE_API_KEY", "").strip()
    if not api_key:
        log("ZeroBounce API key not configured in .env. Skipping email deliverability verification.", "info")
        return records

    target_records = [r for r in records if r.get("email")]
    if not target_records:
        log("No emails found to verify with ZeroBounce.", "info")
        return records

    log(f"Starting ZeroBounce email deliverability verification for {len(target_records)} emails...")

    valid_count = 0
    invalid_count = 0
    zb_credits_used = 0
    cache: Dict[str, Dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=15.0) as client:
        for record in records:
            email = record.get("email", "").strip()
            if not email:
                continue

            if email in cache:
                data = cache[email]
                status = data.get("status", "")
                if status == "valid":
                    record["verification_status"] = "Verified"
                    valid_count += 1
                elif status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
                    record["verification_status"] = "Invalid"
                    record["email"] = ""
                    invalid_count += 1
                elif status in ("catch-all", "catch_all", "unknown"):
                    record["verification_status"] = "Catch-All"
                else:
                    record["verification_status"] = status.capitalize() or "Unverified"
                continue

            try:
                params = {
                    "api_key": api_key,
                    "email": email,
                }
                response = await client.get(ZEROBOUNCE_API_URL, params=params)
                zb_credits_used += 1
                from app.db.database import log_api_credit
                log_api_credit("zerobounce", 1, details=f"ZeroBounce email check for {email}")

                if response.status_code == 200:
                    data = response.json()
                    cache[email] = data
                    status = (data.get("status") or "").lower().strip()
                    sub_status = data.get("sub_status") or ""

                    if status == "valid":
                        record["verification_status"] = "Verified"
                        valid_count += 1
                        log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' is VALID (1 ZeroBounce Credit Used). Total run ZeroBounce credits: {zb_credits_used}", "info")

                    elif status in ("invalid", "spamtrap", "abuse", "do_not_mail"):
                        record["verification_status"] = "Invalid"
                        record["email"] = ""  # Clear invalid email to prevent bounces
                        invalid_count += 1
                        log(
                            f"[ZEROBOUNCE CREDIT LOG] Email '{email}' is {status.upper()} ({sub_status}) (1 ZeroBounce Credit Used). Stripped email to prevent bounces. Total run ZeroBounce credits: {zb_credits_used}",
                            "warning"
                        )

                    elif status in ("catch-all", "catch_all", "unknown"):
                        record["verification_status"] = "Catch-All"
                        log(
                            f"[ZEROBOUNCE CREDIT LOG] Email '{email}' status is {status.upper()} (1 ZeroBounce Credit Used). Marked as Catch-All. Total run ZeroBounce credits: {zb_credits_used}",
                            "info"
                        )

                    else:
                        record["verification_status"] = status.capitalize() or "Unverified"
                        log(f"[ZEROBOUNCE CREDIT LOG] Email '{email}' returned '{status}' (1 ZeroBounce Credit Used). Total run ZeroBounce credits: {zb_credits_used}", "info")

                else:
                    log(f"[ZEROBOUNCE ERROR] API returned status {response.status_code} for '{email}'.", "warning")

            except Exception as exc:
                log(f"[ZEROBOUNCE ERROR] Verification error for '{email}': {exc}", "warning")

    log(f"[CREDIT SUMMARY] ZeroBounce verification completed: Used {zb_credits_used} ZeroBounce Credits for this run ({valid_count} Valid, {invalid_count} Invalid/Stripped).")
    return records
