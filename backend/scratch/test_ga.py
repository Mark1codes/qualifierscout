import asyncio
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '.env'))

from app.schemas import ScrapeStartRequest
from app.scrapers.georgia import GeorgiaScraper

async def test_ga():
    req = ScrapeStartRequest(
        state='Georgia', 
        city='Atlanta', 
        license_type='General Contractor', 
        max_records=10, 
        enrich_leads=False, 
        license_status='Active'
    )
    raw_dir = Path('data/raw')
    
    def log(msg, level='info'): 
        print(f'[{level.upper()}] {msg}')
        
    scraper = GeorgiaScraper(raw_dir)
    records = await scraper.scrape(req, 100, log)
    
    print('\n==================\nGEORGIA RESULTS:\n==================')
    if not records: 
        print('NO RECORDS FOUND.')
    for r in records[:5]:
        print(f'Contractor Name: {r.get("contractor_name")}')
        print(f'License Type:    {r.get("license_type")}')
        print('-'*20)

if __name__ == "__main__":
    asyncio.run(test_ga())
