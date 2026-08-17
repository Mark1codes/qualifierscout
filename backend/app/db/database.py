import sqlite3
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "qualifierscout.sqlite3"


def get_connection() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        "Texas": "TX"
    }
    short_state = state_map.get(state, state)
    with get_connection() as conn:
        rows = conn.execute("SELECT license_number FROM leads WHERE state = ? OR state = ?", (state, short_state)).fetchall()
    return {row["license_number"] for row in rows if row["license_number"]}
