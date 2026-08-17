import asyncio
import httpx
from bs4 import BeautifulSoup
import ddddocr
import re

BASE_URL = "https://public.psiexams.com"

async def test_detail():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE_URL}/search.jsp",
    }
    
    async with httpx.AsyncClient(headers=headers, timeout=30, follow_redirects=True, verify=False) as client:
        # Get Session
        resp = await client.get(f"{BASE_URL}/search.jsp")
        jsessionid = client.cookies.get("JSESSIONID", "")
            
        # Get Captcha
        captcha_resp = await client.get(f"{BASE_URL}/simplecaptcha.jpg;jsessionid={jsessionid}")
        ocr = ddddocr.DdddOcr(show_ad=False)
        captcha_answer = str(ocr.classification(captcha_resp.content)).strip()
        
        # Search for Macias
        payload = {
            "isCompany": "individual",
            "individualOrCompany": "YES",
            "lastName": "Macias",
            "firstName": "Pedro",
            "RecordPerPage": "1000",
            "requestType": "1",
            "captchaAnswer": captcha_answer,
        }
        
        r = await client.post(f"{BASE_URL}/searchLicensee.do", data=payload)
        
        soup = BeautifulSoup(r.text, "html.parser")
        data_table = soup.find(lambda t: t.name == "table" and t.find(lambda tag: tag.name in ["th", "td"] and "Certificate No" in tag.get_text()))
        
        if not data_table:
            print("No table found")
            return
            
        rows = data_table.find_all("tr")
        for row in rows:
            cells = [c.get_text(separator=" ", strip=True) for c in row.find_all(["td", "th"], recursive=False)]
            if len(cells) < 7 or "Certificate No" in cells[1]:
                continue
                
            name = cells[2]
            status = cells[6]
            print(f"Name: {name} | Status: {status}")
            
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
                    
                    detail_resp = await client.post(f"{BASE_URL}{detail_path}", data={"certificateId": cert_id, "certificateApplicationId": cert_app_id})
                    detail_soup = BeautifulSoup(detail_resp.text, "html.parser")
                    
                    candidate_tables = detail_soup.find_all(lambda t: t.name == "table" and t.find(lambda tag: tag.name in ["th", "td"] and "Business Name" in tag.get_text(strip=True)))
                    
                    if candidate_tables:
                        employed_by_table = candidate_tables[-1] 
                        emp_rows = employed_by_table.find_all("tr")
                        for emp_row in emp_rows:
                            emp_cells = [c.get_text(separator=" ", strip=True) for c in emp_row.find_all(["td", "th"])]
                            print(f"Row cells: {emp_cells}")

asyncio.run(test_detail())
