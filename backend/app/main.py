import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import csv
import re
import sqlite3
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.db.database import get_connection, init_db, rows_to_dicts, get_existing_licenses
from app.schemas import ExportRequest, LeadUpdateRequest, ScrapeStartRequest, ScrapeStartResponse
from app.scrapers.north_carolina import NorthCarolinaScraper
from app.services.cleaner import clean_record
from app.services.email_enrichment import enrich_with_email
BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
load_dotenv()
from app.services.linkedin_enrichment import enrich_with_linkedin

try:
    from app.scrapers.florida import FloridaScraper
except ImportError:
    FloridaScraper = None
try:
    from app.scrapers.california import CaliforniaScraper
except ImportError:
    CaliforniaScraper = None
try:
    from app.scrapers.georgia import GeorgiaScraper
except ImportError:
    GeorgiaScraper = None
try:
    from app.scrapers.texas import TexasScraper
except ImportError:
    TexasScraper = None
try:
    from app.scrapers.new_mexico import NewMexicoScraper
except ImportError:
    NewMexicoScraper = None
try:
    from app.scrapers.nevada import NevadaScraper
except ImportError:
    NevadaScraper = None
try:
    from app.scrapers.alaska import AlaskaScraper
except ImportError:
    AlaskaScraper = None
try:
    from app.scrapers.utah import UtahScraper
except ImportError:
    UtahScraper = None
try:
    from app.scrapers.colorado import ColoradoScraper
except ImportError:
    ColoradoScraper = None
try:
    from app.scrapers.arizona import ArizonaScraper
except ImportError:
    ArizonaScraper = None

SCRAPER_REGISTRY = {
    "North Carolina": NorthCarolinaScraper,
    "Florida": FloridaScraper,
    "California": CaliforniaScraper,
    "Georgia": GeorgiaScraper,
    "Texas": TexasScraper,
    "New Mexico": NewMexicoScraper,
    "Nevada": NevadaScraper,
    "Alaska": AlaskaScraper,
    "Utah": UtahScraper,
    "Colorado": ColoradoScraper,
    "Arizona": ArizonaScraper,
}

BASE_DIR = Path(__file__).resolve().parents[1]
RAW_DIR = BASE_DIR / "data" / "raw"
EXPORT_DIR = BASE_DIR / "exports"
EXPORT_COLUMNS = [
    "license_type",
    "license_number",
    "company_name",
    "website",
    "contractor_name",
    "title",
    "email",
    "phone",
    "linkedin",
    "city",
    "state",
]


app = FastAPI(title="QualifierScout API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup() -> None:
    init_db()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "app": "QualifierScout"}


@app.post("/scrape/start", response_model=ScrapeStartResponse)
def start_scrape(request: ScrapeStartRequest, background_tasks: BackgroundTasks) -> ScrapeStartResponse:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scrape_runs
            (state, license_type, city, county, zip_code, license_status, max_records, status, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', 0)
            """,
            (
                request.state,
                request.license_type,
                request.city,
                request.county,
                request.zip_code,
                request.license_status,
                request.max_records,
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.execute("INSERT INTO scrape_logs (run_id, message) VALUES (?, ?)", (run_id, "Scrape queued."))

    background_tasks.add_task(run_scrape, run_id, request)
    return ScrapeStartResponse(run_id=run_id)


@app.get("/scrape/runs/{run_id}")
def get_run(run_id: int) -> dict:
    with get_connection() as conn:
        run = conn.execute("SELECT * FROM scrape_runs WHERE id = ?", (run_id,)).fetchone()
        if not run:
            raise HTTPException(status_code=404, detail="Scrape run not found")
        logs = conn.execute(
            "SELECT message, level, created_at FROM scrape_logs WHERE run_id = ? ORDER BY id ASC", (run_id,)
        ).fetchall()
    payload = dict(run)
    payload["logs"] = rows_to_dicts(logs)
    return payload


STATE_MAP = {
    "Florida": ["Florida", "FL"],
    "California": ["California", "CA"],
    "Georgia": ["Georgia", "GA"],
    "Texas": ["Texas", "TX"],
    "North Carolina": ["North Carolina", "NC"],
    "New Mexico": ["New Mexico", "NM"],
    "Nevada": ["Nevada", "NV"],
    "Alaska": ["Alaska", "AK"],
    "Utah": ["Utah", "UT"],
    "Colorado": ["Colorado", "CO"],
    "Arizona": ["Arizona", "AZ"],
    "FL": ["Florida", "FL"],
    "CA": ["California", "CA"],
    "GA": ["Georgia", "GA"],
    "TX": ["Texas", "TX"],
    "NC": ["North Carolina", "NC"],
    "NM": ["New Mexico", "NM"],
    "NV": ["Nevada", "NV"],
    "AK": ["Alaska", "AK"],
    "UT": ["Utah", "UT"],
    "CO": ["Colorado", "CO"],
    "AZ": ["Arizona", "AZ"],
}


@app.get("/leads")
def get_leads(
    state: str | None = None,
    license_type: str | None = None,
    license_status: str | None = None,
    verification_status: str | None = None,
    duplicates: bool | None = None,
    search: str | None = None,
) -> dict:
    where = []
    params: list[object] = []
    if state and state != "all":
        st_list = STATE_MAP.get(state, [state])
        placeholders = ", ".join(["?"] * len(st_list))
        where.append(f"state IN ({placeholders})")
        params.extend(st_list)
    if license_type:
        where.append("license_type LIKE ?")
        params.append(f"%{license_type}%")
    if license_status:
        where.append("license_status = ?")
        params.append(license_status)
    if verification_status:
        where.append("verification_status = ?")
        params.append(verification_status)
    if duplicates is not None:
        where.append("duplicate_count > 0" if duplicates else "duplicate_count = 0")
    if search:
        where.append("(company_name LIKE ? OR contractor_name LIKE ? OR license_number LIKE ? OR city LIKE ?)")
        query = f"%{search}%"
        params.extend([query, query, query, query])

    sql = "SELECT * FROM leads"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT 500"

    with get_connection() as conn:
        leads = rows_to_dicts(conn.execute(sql, params).fetchall())
        stats = get_stats(conn)
    return {"leads": leads, "stats": stats}


@app.patch("/leads/{lead_id}")
def update_lead(lead_id: int, request: LeadUpdateRequest) -> dict:
    updates = []
    params: list[object] = []
    if request.verification_status is not None:
        updates.append("verification_status = ?")
        params.append(request.verification_status)
    if request.notes is not None:
        updates.append("notes = ?")
        params.append(request.notes)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates supplied")

    params.append(lead_id)
    with get_connection() as conn:
        conn.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = ?", params)
        lead = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
        if not lead:
            raise HTTPException(status_code=404, detail="Lead not found")
    return dict(lead)


@app.post("/leads/import-csv")
async def import_csv_leads(file: UploadFile = File(...)) -> dict:
    if not file.filename or not (file.filename.endswith(".csv") or file.filename.endswith(".xlsx")):
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported.")
    
    contents = await file.read()
    import io
    if file.filename.endswith(".csv"):
        df = pd.read_csv(io.BytesIO(contents))
    else:
        df = pd.read_excel(io.BytesIO(contents))
        
    df.columns = [str(c).strip().lower().replace(" ", "_") for c in df.columns]
    
    records = []
    for _, row in df.iterrows():
        rec = {
            "contractor_name": str(row.get("contractor_name") or row.get("name") or row.get("licensee_name") or "").strip(),
            "company_name": str(row.get("company_name") or row.get("business_name") or "").strip(),
            "license_number": str(row.get("license_number") or row.get("license_num") or row.get("license_#") or "").strip(),
            "license_type": str(row.get("license_type") or row.get("profession") or "General Contractor").strip(),
            "license_status": str(row.get("license_status") or row.get("status") or "Active").strip(),
            "expiration_date": str(row.get("expiration_date") or "").strip(),
            "address": str(row.get("address") or "").strip(),
            "city": str(row.get("city") or "Salt Lake City").strip(),
            "state": str(row.get("state") or "UT").strip(),
            "zip_code": str(row.get("zip_code") or row.get("zip") or "").strip(),
            "phone": str(row.get("phone") or "").strip(),
            "email": str(row.get("email") or "").strip(),
            "website": str(row.get("website") or "").strip(),
            "source_url": "Utah DOPL GRAMA / Bulk Import"
        }
        if rec["contractor_name"] or rec["company_name"] or rec["license_number"]:
            records.append(clean_record(rec))
            
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scrape_runs
            (state, license_type, city, status, progress, total_leads)
            VALUES (?, ?, ?, 'completed', 100, ?)
            """,
            ("Utah", "Bulk CSV Import", "Statewide", len(records)),
        )
        run_id = int(cursor.lastrowid)
        insert_leads(run_id, records)
        update_duplicate_counts()
        
    return {"status": "success", "imported_count": len(records), "run_id": run_id}


@app.post("/export")
def export_leads(request: ExportRequest) -> FileResponse:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    where_clauses = []
    params = []
    if request.verified_only:
        where_clauses.append("verification_status = 'verified'")
    elif request.status and request.status != "all":
        where_clauses.append("verification_status = ?")
        params.append(request.status)
        
    if request.run_id is not None:
        where_clauses.append("run_id = ?")
        params.append(request.run_id)
    if request.state and request.state != "all":
        st_list = STATE_MAP.get(request.state, [request.state])
        placeholders = ", ".join(["?"] * len(st_list))
        where_clauses.append(f"state IN ({placeholders})")
        params.extend(st_list)
    if request.city and request.city != "all":
        where_clauses.append("city = ?")
        params.append(request.city)
    if request.search:
        where_clauses.append("(company_name LIKE ? OR contractor_name LIKE ? OR license_number LIKE ? OR email LIKE ?)")
        query = f"%{request.search}%"
        params.extend([query, query, query, query])
        
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    with get_connection() as conn:
        rows = rows_to_dicts(conn.execute(f"SELECT {', '.join(EXPORT_COLUMNS)} FROM leads {where} ORDER BY id DESC", params).fetchall())


    df = pd.DataFrame(rows, columns=EXPORT_COLUMNS)
    
    # Split contractor_name (or individual company_name fallback) into First Name and Last Name
    first_names = []
    last_names = []
    
    corp_indicators = {
        "INC", "LLC", "CORP", "CORPORATION", "CO", "COMPANY", "LTD", "LIMITED", "PA", "PC", "PLC",
        "CONSTRUCTION", "ROOFING", "PLUMBING", "HVAC", "ELECTRIC", "ELECTRICAL", "DEVELOPMENT",
        "ENTERPRISES", "PARTNERS", "PROPERTIES", "CONTRACTING", "SOLUTIONS", "DESIGN", "DESIGNS",
        "ASSOCIATES", "VENTURES", "HOLDINGS", "INDUSTRIES", "SYSTEMS", "CONTRACTOR", "CONTRACTORS",
        "REMODELING", "SHUTTERS", "WINDOWS", "PAVING", "ENGINEERING", "MASONRY", "RENOVATION",
        "RENOVATIONS", "SERVICES", "SERVICE", "BUILD", "BUILDERS", "GROUP", "AIR", "CONDITIONING"
    }

    for i, row in df.iterrows():
        contractor = str(row.get("contractor_name") or "").strip()
        company = str(row.get("company_name") or "").strip()
        state = str(row.get("state") or "").strip()
        
        target_name = contractor
        # Fallback if contractor_name is missing but company_name is an individual person (e.g., "Acosta, Daniel David")
        if not target_name and company and "," in company:
            words = set(re.findall(r"\b[A-Za-z0-9]+\b", company.upper()))
            if not (words & corp_indicators):
                target_name = company

        if not target_name:
            first_names.append("")
            last_names.append("")
            continue

        if "," in target_name:
            # Format: "LAST, FIRST [MIDDLE]"
            parts = [p.strip() for p in target_name.split(",", 1)]
            last_names.append(parts[0])
            first_names.append(parts[1] if len(parts) > 1 else "")
        else:
            suffixes = {"JR", "JR.", "SR", "SR.", "II", "III", "IV", "V"}
            parts = target_name.split(" ")
            if len(parts) == 1:
                first_names.append(parts[0])
                last_names.append("")
            elif state == "NM":
                # NM format: LAST FIRST MIDDLE
                first_names.append(" ".join(parts[1:]))
                last_names.append(parts[0])
            else:
                # Standard format: FIRST [MIDDLE] LAST [SUFFIX]
                if len(parts) >= 3 and parts[-1].upper() in suffixes:
                    first_names.append(" ".join(parts[:-2]))
                    last_names.append(" ".join(parts[-2:]))
                elif len(parts) >= 2:
                    first_names.append(" ".join(parts[:-1]))
                    last_names.append(parts[-1])
                else:
                    first_names.append(parts[0])
                    last_names.append(" ".join(parts[1:]))
            
    df.insert(4, "First Name", [str(n).title() for n in first_names])
    df.insert(5, "Last Name", [str(n).title() for n in last_names])
    df.drop(columns=[c for c in ["contractor_name", "state", "city"] if c in df.columns], inplace=True)
    
    if request.individuals_only:
        df = df[df["First Name"].str.strip() != ""]
    
    # Format company name to Title Case to prevent ALL CAPS
    df["company_name"] = df["company_name"].apply(lambda x: str(x).title() if pd.notna(x) else x)
    
    # Map headers to exact requested format
    header_mapping = {
        "license_type": "Code",
        "license_number": "License Number",
        "company_name": "Company Name",
        "website": "Website",
        "title": "Title",
        "email": "Email",
        "phone": "Number",
        "linkedin": "LinkedIn Profile",
    }
    df.rename(columns=header_mapping, inplace=True)

    export_path = EXPORT_DIR / f"qualifierscout_export.{request.format}"
    if request.format == "csv":
        df.to_csv(export_path, index=False)
    else:
        df.to_excel(export_path, index=False)

    return FileResponse(export_path, filename=export_path.name)


async def run_scrape(run_id: int, request: ScrapeStartRequest) -> None:
    def log(message: str, level: str = "info") -> None:
        print(f"[{level.upper()}] {message}")
        with get_connection() as conn:
            conn.execute("INSERT INTO scrape_logs (run_id, message, level) VALUES (?, ?, ?)", (run_id, message, level))

    try:
        set_run_status(run_id, "running", 10)
        log(f"Starting {request.state} scrape for {request.license_type}.")
        
        scraper_class = SCRAPER_REGISTRY.get(request.state)
        if not scraper_class:
            raise ValueError(f"Scraper not yet implemented for {request.state}")
            
        scraper = scraper_class(RAW_DIR)
        records = await scraper.scrape(request, run_id, log)
        
        # Validation Filter: strictly drop any junk records that are completely missing a name and license number
        valid_records = [r for r in records if r.get("contractor_name") or r.get("license_number")]
        if len(valid_records) < len(records):
            log(f"Validation Filter: Dropped {len(records) - len(valid_records)} blank/junk records.")
        records = valid_records
        
        # Status Filter: drop leads that don't match the requested active/inactive status
        status_filtered = [r for r in records if request.license_status.lower() in r.get("license_status", request.license_status).lower()]
        if len(status_filtered) < len(records):
            log(f"Status Filter: Dropped {len(records) - len(status_filtered)} leads because they are not {request.license_status}.")
        records = status_filtered
        
        # Smart Filter: drop already scraped leads before enrichment
        existing = get_existing_licenses(request.state)
        new_records = [r for r in records if r.get("license_number") not in existing]
        if len(new_records) < len(records):
            log(f"Smart Pre-Filter: Ignored {len(records) - len(new_records)} leads already in database.")
        records = new_records
        
        set_run_status(run_id, "running", 50)
        
        if request.enrich_leads:
            from app.services.linkedin_enrichment import enrich_with_linkedin
            log("Running Ghost Hunter decision-maker discovery & LinkedIn search...")
            records = await enrich_with_linkedin(records, log)

            from app.services.apollo_enrichment import enrich_with_apollo
            log("Running Premium Apollo API enrichment...")
            records = await enrich_with_apollo(records, log)

            from app.services.zerobounce_verifier import verify_emails_with_zerobounce
            log("Running ZeroBounce email deliverability verification...")
            records = await verify_emails_with_zerobounce(records, log)
        else:
            log("Enrichment disabled. Skipping Ghost Hunter pipeline.")
            
        set_run_status(run_id, "running", 80)

        cleaned = [clean_record(record) for record in records]
        log(f"Cleaned and normalized {len(cleaned)} records.")
        insert_leads(run_id, cleaned)
        update_duplicate_counts()
        refresh_run_totals(run_id)
        set_run_status(run_id, "completed", 100)
        log("Scrape completed.")
    except Exception as exc:
        log(f"Scrape failed: {exc}", "error")
        set_run_status(run_id, "failed", 100)


def set_run_status(run_id: int, status: str, progress: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE scrape_runs SET status = ?, progress = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, progress, run_id),
        )


def insert_leads(run_id: int, records: list[dict]) -> None:
    with get_connection() as conn:
        for record in records:
            conn.execute(
                """
                INSERT INTO leads
                (run_id, contractor_name, company_name, license_number, license_type, license_status,
                 expiration_date, address, city, state, zip_code, phone, email, website, linkedin, title, source_url,
                 verification_status, quality_score, duplicate_key, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    record.get("contractor_name"),
                    record.get("company_name"),
                    record.get("license_number"),
                    record.get("license_type"),
                    record.get("license_status"),
                    record.get("expiration_date"),
                    record.get("address"),
                    record.get("city"),
                    record.get("state"),
                    record.get("zip_code"),
                    record.get("phone"),
                    record.get("email"),
                    record.get("website"),
                    record.get("linkedin"),
                    record.get("title"),
                    record.get("source_url"),
                    "verified" if record.get("quality_score", 0) >= 80 and record.get("email") else "needs_review",
                    record.get("quality_score", 60),
                    record.get("duplicate_key"),
                    record.get("notes"),
                ),
            )


def update_duplicate_counts() -> None:
    with get_connection() as conn:
        keys = conn.execute(
            "SELECT duplicate_key, COUNT(*) AS count FROM leads GROUP BY duplicate_key HAVING count > 1"
        ).fetchall()
        conn.execute("UPDATE leads SET duplicate_count = 0")
        for row in keys:
            conn.execute(
                "UPDATE leads SET duplicate_count = ? WHERE duplicate_key = ?",
                (int(row["count"]) - 1, row["duplicate_key"]),
            )


def refresh_run_totals(run_id: int) -> None:
    with get_connection() as conn:
        totals = conn.execute(
            """
            SELECT
              COUNT(*) AS total_records,
              COUNT(DISTINCT duplicate_key) AS unique_leads,
              SUM(CASE WHEN duplicate_count > 0 THEN 1 ELSE 0 END) AS duplicate_leads,
              SUM(CASE WHEN verification_status = 'verified' THEN 1 ELSE 0 END) AS verified_leads
            FROM leads
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE scrape_runs
            SET total_records = ?, unique_leads = ?, duplicate_leads = ?, verified_leads = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                totals["total_records"] or 0,
                totals["unique_leads"] or 0,
                totals["duplicate_leads"] or 0,
                totals["verified_leads"] or 0,
                run_id,
            ),
        )


def get_stats(conn: sqlite3.Connection) -> dict:
    row = conn.execute(
        """
        SELECT
          COUNT(*) AS total,
          COUNT(DISTINCT duplicate_key) AS unique_leads,
          SUM(CASE WHEN duplicate_count > 0 THEN 1 ELSE 0 END) AS duplicates,
          SUM(CASE WHEN verification_status = 'verified' THEN 1 ELSE 0 END) AS verified,
          SUM(CASE WHEN verification_status = 'needs_review' THEN 1 ELSE 0 END) AS needs_review,
          SUM(CASE WHEN verification_status = 'not_verified' THEN 1 ELSE 0 END) AS not_verified
        FROM leads
        """
    ).fetchone()
    return {key: row[key] or 0 for key in row.keys()}
