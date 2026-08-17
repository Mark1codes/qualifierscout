import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

from app.schemas import ScrapeStartRequest
from app.scrapers.texas import TexasScraper
from app.scrapers.florida import FloridaScraper
from app.scrapers.north_carolina import NorthCarolinaScraper
from app.scrapers.georgia import GeorgiaScraper

async def run_test():
    raw_dir = Path('data/raw')
    raw_dir.mkdir(parents=True, exist_ok=True)
    
    def log(msg, level='info'):
        pass # Silence logs for cleaner output, we will print records

    states_to_test = [
        ("Texas", TexasScraper, {"city": "Dallas", "license_type": "General Contractor"}),
        ("Florida", FloridaScraper, {"city": "Miami", "license_type": "General Contractor"}), 
        ("North Carolina", NorthCarolinaScraper, {"city": "Charlotte", "license_type": "Building"}),
        ("Georgia", GeorgiaScraper, {"city": "Atlanta", "license_type": ""}), # Leave license type empty to avoid exact match errors
    ]

    for state_name, scraper_class, params in states_to_test:
        print(f"\n======================================")
        print(f"TESTING {state_name.upper()} SCRAPER")
        print(f"======================================")
        scraper = scraper_class(raw_dir)
        
        req = ScrapeStartRequest(
            state=state_name,
            city=params["city"],
            license_type=params["license_type"],
            max_records=5,
            enrich_leads=False,
            license_status='Active'
        )
        
        try:
            records = await scraper.scrape(req, 5, log)
            if records:
                print(f"[SUCCESS] Found {len(records)} records!")
                for r in records[:3]:
                    print(f"  - Company: {r.get('company_name', 'N/A')} | Contractor: {r.get('contractor_name', 'N/A')} | Lic: {r.get('license_number', 'N/A')}")
            else:
                print("[FAILED] 0 records returned.")
        except Exception as e:
            print(f"[ERROR] {e}")

if __name__ == "__main__":
    asyncio.run(run_test())
