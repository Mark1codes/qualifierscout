"""
Test New Mexico CAPTCHA solving using OCR.space free API.
No extra packages needed - just httpx + base64 (standard library).
"""
import asyncio
import httpx
import base64
import json

BASE = "https://public.psiexams.com"
OCR_API_URL = "https://api.ocr.space/parse/image"

async def solve_captcha_with_ocr(captcha_bytes: bytes) -> str:
    """Send CAPTCHA image to OCR.space and return the text."""
    b64 = base64.b64encode(captcha_bytes).decode()
    data_url = f"data:image/png;base64,{b64}"
    
    # OCR.space free demo key
    payload = {
        "base64Image": data_url,
        "apikey": "helloworld",   # public demo key
        "language": "eng",
        "isOverlayRequired": False,
        "scale": True,
        "isTable": False,
        "OCREngine": 1,
    }
    
    async with httpx.AsyncClient(timeout=20) as ocr_client:
        resp = await ocr_client.post(OCR_API_URL, data=payload)
        result = resp.json()
        
    if result.get("IsErroredOnProcessing"):
        print(f"  OCR error: {result.get('ErrorMessage')}")
        return ""
    
    parsed = result.get("ParsedResults", [{}])
    text = parsed[0].get("ParsedText", "").strip()
    # Clean up - remove spaces, newlines, common OCR noise
    text = text.replace(" ", "").replace("\n", "").replace("\r", "")
    return text


async def test_nm_with_ocr():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": f"{BASE}/search.jsp",
    }

    async with httpx.AsyncClient(
        headers=headers, timeout=30, follow_redirects=True, verify=False,
    ) as client:
        # Step 1: GET page to establish session
        print("Step 1: Getting NM portal page...")
        resp = await client.get(f"{BASE}/search.jsp")
        jsessionid = resp.cookies.get("JSESSIONID", "")
        print(f"  JSESSIONID: {jsessionid}")

        # Step 2: Download CAPTCHA image
        print("Step 2: Downloading CAPTCHA...")
        captcha_resp = await client.get(f"{BASE}/simplecaptcha.jpg;jsessionid={jsessionid}")
        captcha_bytes = captcha_resp.content
        print(f"  Got {len(captcha_bytes)} bytes")

        # Step 3: Solve CAPTCHA via OCR.space
        print("Step 3: Sending to OCR.space...")
        captcha_answer = await solve_captcha_with_ocr(captcha_bytes)
        print(f"  OCR result: '{captcha_answer}'")

        if not captcha_answer:
            print("  OCR failed! Cannot proceed.")
            return

        # Step 4: Submit the search form with the solved CAPTCHA
        print(f"Step 4: Submitting form with CAPTCHA='{captcha_answer}'...")
        payload = {
            "isCompany": "company",
            "companyType": "445",        # 445 = Contractor
            "businessName": "",
            "businessCity": "Albuquerque",
            "businessZipCode": "",
            "requestType": "1",
            "captchaAnswer": captcha_answer,
        }
        r = await client.post(f"{BASE}/searchLicensee.do", data=payload)
        print(f"  Search status: {r.status_code}, Length: {len(r.text)}")

        low = r.text.lower()
        if "captcha" in low and "<tr" not in low:
            print("  CAPTCHA was WRONG - OCR failed to read it correctly")
        elif "no records" in low:
            print("  CAPTCHA correct! No contractors found in Albuquerque")
        elif "<tr" in low:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(r.text, "html.parser")
            # Find result table
            tables = soup.find_all("table")
            data_rows = []
            for t in tables:
                rows = t.find_all("tr")
                if len(rows) > 2:
                    for row in rows[:5]:
                        cells = [c.get_text(strip=True) for c in row.find_all(["td","th"])]
                        if cells:
                            data_rows.append(cells)
            print(f"  CAPTCHA correct! Found result table rows:")
            for row in data_rows[:5]:
                print(f"    {row}")
        else:
            print(f"  Unexpected response: {r.text[:300]}")


if __name__ == "__main__":
    asyncio.run(test_nm_with_ocr())
