import asyncio
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Callable
from bs4 import BeautifulSoup
from app.schemas import ScrapeStartRequest

class GeorgiaScraper:
    def __init__(self, raw_data_dir: Path):
        self.raw_data_dir = raw_data_dir
        self.raw_data_dir.mkdir(parents=True, exist_ok=True)
        self.state_code = "georgia"
        self.api_key = os.environ.get('ZENROWS_API_KEY')

    async def scrape(
        self,
        request: ScrapeStartRequest,
        total_limit: int,
        log: Callable[[str, str], None]
    ) -> List[Dict]:
        records = []
        if not self.api_key:
            log("ZENROWS_API_KEY not found in environment. Georgia requires ZenRows to bypass Cloudflare Turnstile.", "error")
            return records
            
        log("Starting Georgia scrape using ZenRows API (Cloudflare Bypass)...", "info")
        target_url = "https://verify.sos.ga.gov/Verification/Search.aspx"
        
        # JS instructions to fill out the ASP.NET form and click Search
        js_instructions = [
            {'wait_for': '#t_web_lookup__license_type_name'},
            {'evaluate': f"document.getElementById('t_web_lookup__license_type_name').value = '{request.license_type}';"},
            {'evaluate': f"document.getElementById('t_web_lookup__addr_city').value = '{request.city or ''}';"},
            {'evaluate': "document.getElementById('sch_button').click();"},
            {'wait_for': '#datagrid_results'}  # Wait for the results grid to render
        ]
        
        params = {
            'apikey': self.api_key,
            'url': target_url,
            'js_render': 'true',
            'premium_proxy': 'true',
            'proxy_country': 'us',
            'js_instructions': json.dumps(js_instructions)
        }
        
        zenrows_url = 'https://api.zenrows.com/v1/'
        
        try:
            # We use httpx to call ZenRows API asynchronously
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                log(f"Sending JS instructions to ZenRows for {request.license_type} in {request.city or 'All Cities'}...", "info")
                r = await client.get(zenrows_url, params=params)
                
                if r.status_code == 200:
                    html = r.text
                    soup = BeautifulSoup(html, 'html.parser')
                    grid = soup.find('table', id='datagrid_results')
                    
                    if grid:
                        rows = grid.find_all('tr')
                        # Skip header row
                        for row in rows[1:]:
                            cells = row.find_all('td')
                            if len(cells) >= 3:
                                name_text = cells[0].get_text(strip=True)
                                license_type = cells[1].get_text(strip=True)
                                status = cells[2].get_text(strip=True)
                                
                                # Basic name parsing (Last, First) -> (First Last)
                                parsed_name = name_text
                                if ',' in name_text:
                                    parts = name_text.split(',', 1)
                                    parsed_name = f"{parts[1].strip()} {parts[0].strip()}"
                                    
                                record = {
                                    "state": "Georgia",
                                    "contractor_name": parsed_name,
                                    "company_name": "", # Often not provided separately in GA list view
                                    "license_number": "", 
                                    "license_type": license_type,
                                    "license_status": status,
                                    "city": request.city or ""
                                }
                                records.append(record)
                                if len(records) >= total_limit:
                                    break
                                    
                        log(f"ZenRows returned {len(records)} records.", "success")
                    else:
                        if 'No matching records' in html or 'SearchResults' in html:
                            log("ZenRows successfully bypassed Cloudflare, but found 0 matching records for this search criteria.", "warn")
                        else:
                            log("ZenRows bypassed Cloudflare, but results grid was not found (possible layout change or Turnstile block).", "warn")
                else:
                    log(f"ZenRows API Error: {r.status_code} - {r.text[:100]}", "error")
                    
        except Exception as e:
            log(f"Error communicating with ZenRows: {str(e)}", "error")
            
        return records
