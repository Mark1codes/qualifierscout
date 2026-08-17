import asyncio
import sys
import os
from pathlib import Path

# Add backend to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.schemas import ScrapeStartRequest
from app.scrapers.texas import TexasScraper

async def test_tx():
    req = ScrapeStartRequest(
        state='Texas', 
        city='Houston', 
        license_type='General Contractor', 
        max_records=10, 
        enrich_leads=False, 
        license_status='Active'
    )
    raw_dir = Path('data/raw')
    
    def log(msg, level='info'): 
        print(f'[{level.upper()}] {msg}')
        
    scraper = TexasScraper(raw_dir)
    records = await scraper.scrape(req, 100, log)
    
    print('\n==================\nTEXAS RESULTS:\n==================')
    if not records: 
        print('NO RECORDS FOUND.')
    for r in records[:5]:
        print(f'Contractor Name: {r.get("contractor_name")}')
        print(f'Company Name:    {r.get("company_name")}')
        print(f'License Num:     {r.get("license_number")}')
        print(f'Triangulation:   {"READY" if r.get("company_name") else "NAME ONLY"}')
        print('-'*20)

if __name__ == "__main__":
    asyncio.run(test_tx())
