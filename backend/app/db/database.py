import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "qualifierscout.sqlite3"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA temp_store = MEMORY;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn



def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scrape_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                state TEXT NOT NULL,
                license_type TEXT NOT NULL,
                city TEXT,
                county TEXT,
                zip_code TEXT,
                license_status TEXT,
                max_records INTEGER NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                total_records INTEGER NOT NULL DEFAULT 0,
                unique_leads INTEGER NOT NULL DEFAULT 0,
                duplicate_leads INTEGER NOT NULL DEFAULT 0,
                verified_leads INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scrape_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                message TEXT NOT NULL,
                level TEXT NOT NULL DEFAULT 'info',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES scrape_runs(id)
            );

            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                contractor_name TEXT,
                company_name TEXT,
                license_number TEXT,
                license_type TEXT,
                license_status TEXT,
                expiration_date TEXT,
                address TEXT,
                city TEXT,
                state TEXT,
                zip_code TEXT,
                phone TEXT,
                email TEXT,
                website TEXT,
                linkedin TEXT,
                title TEXT,
                source_url TEXT,
                verification_status TEXT NOT NULL DEFAULT 'not_verified',
                quality_score INTEGER NOT NULL DEFAULT 60,
                duplicate_key TEXT,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                notes TEXT,
                date_scraped TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(run_id) REFERENCES scrape_runs(id)
            );

            CREATE TABLE IF NOT EXISTS api_credit_tracker (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                credits_used INTEGER NOT NULL DEFAULT 1,
                run_id INTEGER,
                details TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        # Safe migration: add linkedin column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN linkedin TEXT")
        except Exception:
            pass  # Column already exists
            
        # Safe migration: add title column if it doesn't exist yet
        try:
            conn.execute("ALTER TABLE leads ADD COLUMN title TEXT")
        except Exception:
            pass  # Column already exists

        # Seed initial credit totals if tracker table is newly created
        count = conn.execute("SELECT COUNT(*) FROM api_credit_tracker").fetchone()[0]
        if count == 0:
            conn.execute(
                "INSERT INTO api_credit_tracker (service, credits_used, details) VALUES (?, ?, ?)",
                ("apollo", 870, "Initial baseline Apollo credit usage"),
            )
            conn.execute(
                "INSERT INTO api_credit_tracker (service, credits_used, details) VALUES (?, ?, ?)",
                ("zerobounce", 587, "Initial baseline ZeroBounce credit usage"),
            )
            conn.execute(
                "INSERT INTO api_credit_tracker (service, credits_used, details) VALUES (?, ?, ?)",
                ("apollo_search", 3685, "Initial baseline Apollo search requests"),
            )


def log_api_credit(service: str, credits_used: int = 1, run_id: int | None = None, details: str = "") -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO api_credit_tracker (service, credits_used, run_id, details) VALUES (?, ?, ?, ?)",
                (service, credits_used, run_id, details),
            )
    except Exception:
        pass


def get_total_api_credits() -> dict[str, int]:
    with get_connection() as conn:
        apollo_credits = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'apollo'"
        ).fetchone()[0]
        zerobounce_credits = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'zerobounce'"
        ).fetchone()[0]
        apollo_requests = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'apollo_search'"
        ).fetchone()[0]
    return {
        "apollo_credits": apollo_credits,
        "zerobounce_credits": zerobounce_credits,
        "apollo_requests": apollo_requests,
    }


def get_run_api_credits(run_id: int) -> dict[str, int]:
    with get_connection() as conn:
        apollo_credits = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'apollo' AND run_id = ?", (run_id,)
        ).fetchone()[0]
        zerobounce_credits = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'zerobounce' AND run_id = ?", (run_id,)
        ).fetchone()[0]
        apollo_requests = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM api_credit_tracker WHERE service = 'apollo_search' AND run_id = ?", (run_id,)
        ).fetchone()[0]
    return {
        "apollo_credits": apollo_credits,
        "zerobounce_credits": zerobounce_credits,
        "apollo_requests": apollo_requests,
    }



def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]

def get_existing_licenses(state: str) -> set[str]:
    """Return a set of all license numbers already in the database for a given state."""
    state_map = {
        "New Mexico": "NM",
        "North Carolina": "NC",
        "Florida": "FL",
        "California": "CA",
        "Georgia": "GA",
        "Texas": "TX",
        "Nevada": "NV",
        "Alaska": "AK",
        "Arizona": "AZ",
        "Colorado": "CO",
        "Utah": "UT",
    }
    short_state = state_map.get(state, state)
    with get_connection() as conn:
        rows = conn.execute("SELECT license_number FROM leads WHERE state = ? OR state = ?", (state, short_state)).fetchall()
    return {row["license_number"] for row in rows if row["license_number"]}
