import asyncio
import base64
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup
import httpx

from app.schemas import ScrapeStartRequest

BASE_URL = "https://public.psiexams.com"
OCR_API_URL = "https://api.ocr.space/parse/image"
MAX_RETRIES = 10

class NewMexicoScraper:
    name = "New Mexico PSI"

    def __init__(self, raw_dir: Path) -> None:
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)

    async def _solve_captcha_with_ocr(self, captcha_bytes: bytes, log) -> str:
        try:
            import ddddocr
            ocr = ddddocr.DdddOcr(show_ad=False)
            res = ocr.classification(captcha_bytes)
            return str(res).strip()
        except ImportError:
            log("ddddocr not installed. Please run: pip install ddddocr", "error")
            return ""
        except Exception as e:
            log(f"Local OCR failed: {e}", "warning")
            return ""

    async def scrape(self, request: ScrapeStartRequest, run_id: int, log) -> list[dict]:
        log("Opening New Mexico license search...")
        records = await self._try_public_search(request, log)
        if not records:
            log("Live source returned no records or was blocked.", "warning")

        records = records[: request.max_records]
        raw_path = self.raw_dir / f"run_{run_id}_new_mexico_raw.json"
        raw_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
        log(f"Saved {len(records)} raw records to {raw_path.name}.")
        return records

    async def _try_public_search(self, request: ScrapeStartRequest, log) -> list[dict]:
        city = (request.city or "Albuquerque").title()
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": f"{BASE_URL}/search.jsp",
        }

        records = []
        
        async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True, verify=False) as client:
            for attempt in range(1, MAX_RETRIES + 1):
                log(f"Attempt {attempt}/{MAX_RETRIES}: Establishing NM portal session...")
                
                try:
                    resp = await client.get(f"{BASE_URL}/search.jsp")
                    jsessionid = client.cookies.get("JSESSIONID", "")
                    
                    if not jsessionid:
                        log("Failed to get JSESSIONID, retrying...", "warning")
                        continue
                        
                    log(f"Attempt {attempt}: Downloading CAPTCHA...")
                    captcha_resp = await client.get(f"{BASE_URL}/simplecaptcha.jpg;jsessionid={jsessionid}")
                    captcha_bytes = captcha_resp.content
                    
                    log(f"Attempt {attempt}: Sending CAPTCHA to OCR service...")
                    captcha_answer = await self._solve_captcha_with_ocr(captcha_bytes, log)
                    
                    if not captcha_answer:
                        log(f"Attempt {attempt}: OCR failed to read text, retrying...", "warning")
                        continue
                        
                    import string
                    
                    log(f"Attempt {attempt}: OCR solved as '{captcha_answer}'. Iterating A-Z to bypass 300-record limit...")
                    
                    all_records = []
                    
                    for letter in string.ascii_lowercase:
                        payload = {
                            "isCompany": "individual",
                            "individualOrCompany": "YES",
                            "lastName": letter,
                            "indCity": city,
                            "RecordPerPage": "1000",
                            "requestType": "1",
                            "captchaAnswer": captcha_answer,
                        }
                        
                        r = await client.post(f"{BASE_URL}/searchLicensee.do", data=payload)
                        low = r.text.lower()
                        
                        if letter == "a":
                            if "captcha" in low or "error" in low or "invalid" in low:
                                log(f"Attempt {attempt}: CAPTCHA was incorrect. Retrying...", "warning")
                                break # Break inner loop, retry outer loop
                                
                            # If there's no table at all on 'A', the CAPTCHA likely failed silently
                            soup = BeautifulSoup(r.text, "html.parser")
                            data_table = soup.find(lambda t: t.name == "table" and t.find(lambda tag: tag.name in ["th", "td"] and "Certificate No" in tag.get_text()))
                            if not data_table:
                                log(f"Attempt {attempt}: CAPTCHA was likely incorrect (no data table found). Retrying...", "warning")
                                break
                            
                        if "<tr" in low:
                            soup = BeautifulSoup(r.text, "html.parser")
                            
                            # Find the table that contains "Certificate No" in a th or td
                            data_table = soup.find(lambda t: t.name == "table" and t.find(lambda tag: tag.name in ["th", "td"] and "Certificate No" in tag.get_text()))
                            
                            if data_table:
                                rows = data_table.find_all("tr")
                                for row in rows:
                                    # recursive=False ensures we don't grab cells from nested tables
                                    cells = [c.get_text(separator=" ", strip=True) for c in row.find_all(["td", "th"], recursive=False)]
                                    
                                    # Skip header row or pagination rows which don't have enough columns
                                    if len(cells) < 7 or "Certificate No" in cells[1]:
                                        continue
                                        
                                    lic_num = cells[1]
                                    name = cells[2]
                                    classification = cells[3]
                                    status = cells[6]
                                    
                                    # New Mexico uses "Attached"/"Unattached" for active individuals. Map this for the UI.
                                    if status.strip().lower() in ["attached", "unattached"]:
                                        status = "Active"
                                    
                                    # Strict Filtering based on requested Trade
                                    req_type = request.license_type.lower()
                                    if "railroad" in req_type:
                                        # Strictly limit to Railroad (GF06), Underground (GF09), and Master Heavy Civil (GF98)
                                        is_valid = any(code in classification.replace("-", "") for code in ["GF06", "GF09", "GF98"])
                                        if not is_valid:
                                            continue
                                            
                                    elif "general contractor" in req_type and "GB" not in classification:
                                        continue
                                        
                                    elif "electrical contractor" in req_type and "EE" not in classification and "ER" not in classification:
                                        continue
                                        
                                    elif "plumbing" in req_type and "MM" not in classification:
                                        continue
                                        
                                    elif "roofing" in req_type and "GS" not in classification:
                                        # GS-21 is Roofing in NM, but keeping GS to be safe
                                        continue
                                        
                                    elif "hvac" in req_type and "MM" not in classification:
                                        # MM-3 is HVAC in NM
                                        continue

                                    company_name = ""
                                    actual_lic_num = lic_num
                                    source_url = BASE_URL
                                    
                                    a_tag = row.find("a")
                                    if a_tag and "onclick" in a_tag.attrs:
                                        onclick_str = a_tag["onclick"]
                                        
                                        url_match = re.search(r'showLicensee\("([^"]+)"', onclick_str)
                                        cert_id_match = re.search(r'certificateId:\s*"([^"]+)"', onclick_str)
                                        cert_app_id_match = re.search(r'certificateApplicationId:\s*"([^"]+)"', onclick_str)
                                        
                                        if url_match and cert_id_match:
                                            detail_path = url_match.group(1)
                                            cert_id = cert_id_match.group(1)
                                            cert_app_id = cert_app_id_match.group(1) if cert_app_id_match else ""
                                            
                                            detail_url = f"{BASE_URL}{detail_path}?certificateId={cert_id}"
                                            if cert_app_id:
                                                detail_url += f"&certificateApplicationId={cert_app_id}"
                                                
                                            source_url = detail_url
                                            
                                            try:
                                                # Delay to avoid overloading server
                                                await asyncio.sleep(0.5)
                                                
                                                # POST request is required by the portal for detail pages
                                                detail_resp = await client.post(f"{BASE_URL}{detail_path}", data={"certificateId": cert_id, "certificateApplicationId": cert_app_id})
                                                detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                                                
                                                # Find all tables containing "Business Name" and get the innermost one
                                                candidate_tables = detail_soup.find_all(lambda t: t.name == "table" and t.find(lambda tag: tag.name in ["th", "td"] and "Business Name" in tag.get_text(strip=True)))
                                                
                                                if candidate_tables:
                                                    employed_by_table = candidate_tables[-1]
                                                    emp_rows = employed_by_table.find_all("tr")
                                                    for emp_row in emp_rows:
                                                        emp_cells = [c.get_text(separator=" ", strip=True) for c in emp_row.find_all(["td", "th"])]
                                                        # Make sure it's a data row, not the header row
                                                        if len(emp_cells) >= 2 and "Business Name" not in emp_cells[0]:
                                                            company_name = emp_cells[0]
                                                            actual_lic_num = emp_cells[1]
                                                            break
                                            except Exception as e:
                                                log(f"Failed to fetch detail page for {name}: {e}", "warning")

                                    all_records.append({
                                        "source_url": source_url,
                                        "contractor_name": name,
                                        "company_name": company_name, 
                                        "license_number": actual_lic_num,
                                        "license_type": classification,
                                        "license_status": status,
                                        "city": city,
                                        "state": "NM",
                                    })
                                    
                            if len(all_records) >= request.max_records:
                                break
                                    
                    if len(all_records) > 0 or letter == "z":
                        log(f"Extracted {len(all_records)} records from page across A-Z.")
                        return all_records
                    
                    # If we broke early because of captcha, we loop again
                    if letter == "a":
                        await asyncio.sleep(1)
                        continue
                        
                    log(f"Attempt {attempt}: Unexpected response from server.", "warning")
                    
                except Exception as exc:
                    log(f"Attempt {attempt} failed due to network/parsing error: {exc}", "warning")
                    await asyncio.sleep(2)

        log("Max retries reached for New Mexico CAPTCHA. Scrape failed.", "error")
        return []
