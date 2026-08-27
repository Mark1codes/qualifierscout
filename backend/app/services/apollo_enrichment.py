import os
import httpx
from typing import List, Dict, Any

async def enrich_with_apollo(records: List[Dict[str, Any]], log) -> List[Dict[str, Any]]:
    api_key = os.getenv("APOLLO_API_KEY", "").strip()
    if not api_key:
        log("No Apollo API key found in .env. Skipping premium enrichment.", "warning")
        return records

    url = "https://api.apollo.io/api/v1/people/match"
    apollo_credits_used = 0
    
    async with httpx.AsyncClient(timeout=30) as client:
        for record in records:
            name = record.get("contractor_name", "").strip()
            if not name:
                continue
                
            parts = name.split(" ")
            first_name = parts[0]
            last_name = " ".join(parts[1:]) if len(parts) > 1 else ""
            
            if record.get("state") == "NM" and len(parts) > 1:
                first_name, last_name = last_name, first_name
            
            has_linkedin = bool(record.get("linkedin"))
            has_company = bool(record.get("company_name"))
            if record.get("state") == "NM" and not has_linkedin and not has_company:
                log(f"[APOLLO CREDIT LOG] Skipping Apollo for {name} (No LinkedIn/Company found). Saves 1 credit.")
                continue
                
            payload = {
                "first_name": first_name,
                "last_name": last_name,
                "reveal_personal_emails": True
            }
            if has_linkedin:
                payload["linkedin_url"] = record.get("linkedin")
            if has_company:
                payload["organization_name"] = record.get("company_name")
            
            headers = {
                "X-Api-Key": api_key,
                "Content-Type": "application/json"
            }
            
            try:
                log(f"[APOLLO LOG] Searching Apollo database for: {name}...")
                from app.db.database import log_api_credit
                log_api_credit("apollo_search", 1, details=f"Apollo search query for {name}")

                response = await client.post(url, json=payload, headers=headers)
                
                if response.status_code == 200:
                    data = response.json()
                    person = data.get("person", {})
                    
                    if person:
                        # Extract Email (Strictly Verified Only)
                        email = person.get("email") or person.get("personal_email")
                        email_status = (person.get("email_status") or "").lower()
                        
                        if email:
                            if email_status == "verified" or email_status == "extrapolated":
                                record["email"] = email
                                apollo_credits_used += 1
                                log_api_credit("apollo", 1, details=f"Apollo verified email for {name}: {email}")
                                log(f"[APOLLO CREDIT LOG] Unlocked verified email for '{name}': {email} (1 Apollo Credit Used). Total run Apollo credits: {apollo_credits_used}")
                            else:
                                log(f"[APOLLO LOG] Apollo found email '{email}' but status was '{email_status}'. Ignoring to prevent bounces.")
                                
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
                            record["_drop_lead"] = True
                            
                    else:
                        log(f"[APOLLO LOG] Apollo found no match for {name}.")
                else:
                    log(f"[APOLLO ERROR] Apollo API Error {response.status_code}: {response.text}", "error")
            except Exception as e:
                log(f"[APOLLO ERROR] Apollo Request Failed: {e}", "error")
                
    # Filter out the leads we tagged for removal
    valid_records = [r for r in records if not r.get("_drop_lead")]
    dropped_count = len(records) - len(valid_records)
    if dropped_count > 0:
        log(f"[APOLLO LOG] Apollo Enrichment dropped {dropped_count} leads due to blacklisted company names.")
        
    log(f"[CREDIT SUMMARY] Apollo Enrichment Finished: Used {apollo_credits_used} Apollo Credits for this run.")
    return valid_records
