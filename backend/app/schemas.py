from typing import Literal

from pydantic import BaseModel, Field


VerificationStatus = Literal["verified", "needs_review", "not_verified", "rejected"]
RunStatus = Literal["queued", "running", "completed", "failed"]


class ScrapeStartRequest(BaseModel):
    state: str = Field(default="North Carolina")
    license_type: str = Field(default="General Contractor")
    city: str | None = None
    county: str | None = None
    zip_code: str | None = None
    license_status: str = Field(default="Active")
    max_records: int = Field(default=50, ge=1, le=500)
    enrich_leads: bool = Field(default=True)


class ScrapeStartResponse(BaseModel):
    run_id: int


class LeadUpdateRequest(BaseModel):
    verification_status: VerificationStatus | None = None
    notes: str | None = None


class ExportRequest(BaseModel):
    format: Literal["csv", "xlsx"] = "csv"
    verified_only: bool = True
    run_id: int | None = None
    state: str | None = None
    city: str | None = None
    status: str | None = None
    search: str | None = None

