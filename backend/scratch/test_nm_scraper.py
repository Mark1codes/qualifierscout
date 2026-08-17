import asyncio
from pathlib import Path
from app.schemas import ScrapeStartRequest
from app.scrapers.new_mexico import NewMexicoScraper

def log(msg, level="info"):
    print(f"[{level.upper()}] {msg}")

async def test():
    req = ScrapeStartRequest(
        state="New Mexico",
        license_type="General Contractor",
        city="Albuquerque",
        license_status="Active",
        max_records=5
    )
    raw_dir = Path("./data/raw")
    scraper = NewMexicoScraper(raw_dir)
    records = await scraper.scrape(req, run_id=999, log=log)
    print("Found records:")
    for r in records:
        print(r)

if __name__ == "__main__":
    asyncio.run(test())
