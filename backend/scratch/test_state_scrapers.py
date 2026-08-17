"""
Test FL, GA, TX scrapers - NO Apollo credits used.
Run from: backend/ directory
Usage: python scratch/test_state_scrapers.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pathlib import Path
from app.schemas import ScrapeStartRequest

RAW_DIR = Path("data/raw")


def make_req(state, city, max_records=5):
    return ScrapeStartRequest(
        state=state,
        city=city,
        license_type="General Contractor",
        license_status="Active",
        max_records=max_records,
        enrich_leads=False,
        county=None,
        zip_code=None,
    )


def log(msg, level="info"):
    tag = {"info": "[OK]", "warning": "[WARN]", "error": "[ERR]"}.get(level, "[-]")
    print(f"  {tag} {msg}")


def show_results(state, records):
    print(f"\n{'='*55}")
    print(f"  {state} -- {len(records)} records")
    print(f"{'='*55}")
    if not records:
        print("  [FAIL] No records returned!")
        return
    for i, r in enumerate(records[:5]):
        cname = r.get("contractor_name") or "BLANK"
        coname = r.get("company_name") or "BLANK"
        licno = r.get("license_number") or "BLANK"
        ready = "TRIANGULATION READY" if r.get("company_name") else "NAME ONLY (no company)"
        print(f"\n  Record #{i+1}:")
        print(f"    contractor_name = {cname}")
        print(f"    company_name    = {coname}")
        print(f"    license_number  = {licno}")
        print(f"    status          = {ready}")


async def test_florida():
    print("\n[FLORIDA] Starting test...")
    try:
        from app.scrapers.florida import FloridaScraper
        records = await FloridaScraper(RAW_DIR).scrape(make_req("Florida", "Miami"), 9001, log)
        show_results("FLORIDA", records)
    except Exception as e:
        print(f"  [ERR] Florida FAILED: {e}")


async def test_georgia():
    print("\n[GEORGIA] Starting test...")
    try:
        from app.scrapers.georgia import GeorgiaScraper
        records = await GeorgiaScraper(RAW_DIR).scrape(make_req("Georgia", "Atlanta"), 9002, log)
        show_results("GEORGIA", records)
    except Exception as e:
        print(f"  [ERR] Georgia FAILED: {e}")


async def test_texas():
    print("\n[TEXAS] Starting test (fetches detail pages - may take ~30s)...")
    try:
        from app.scrapers.texas import TexasScraper
        records = await TexasScraper(RAW_DIR).scrape(make_req("Texas", "Austin", max_records=3), 9003, log)
        show_results("TEXAS", records)
    except Exception as e:
        print(f"  [ERR] Texas FAILED: {e}")


async def main():
    print("=" * 55)
    print("  QualifierScout - Triangulation Test (No Apollo)")
    print("=" * 55)

    await test_florida()
    await test_georgia()
    await test_texas()

    print("\n" + "=" * 55)
    print("  All tests complete.")
    print("=" * 55)


if __name__ == "__main__":
    asyncio.run(main())
