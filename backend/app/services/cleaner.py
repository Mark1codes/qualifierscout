import re
from hashlib import sha1


def normalize_space(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_phone(value: str | None) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return normalize_space(value)


def duplicate_key(record: dict) -> str:
    identity = "|".join(
        [
            normalize_space(record.get("license_number")).lower(),
            normalize_space(record.get("company_name")).lower(),
            normalize_space(record.get("contractor_name")).lower(),
            normalize_space(record.get("state")).lower(),
        ]
    )
    return sha1(identity.encode("utf-8")).hexdigest()


def quality_score(record: dict) -> int:
    score = 35
    if record.get("license_number"):
        score += 15
    if record.get("license_status", "").lower() == "active":
        score += 15
    if record.get("phone"):
        score += 10
    if record.get("address"):
        score += 8
    if record.get("company_name"):
        score += 7
    if record.get("source_url"):
        score += 5
    if record.get("email") or record.get("website"):
        score += 5
    return min(score, 100)


def clean_record(record: dict) -> dict:
    cleaned = {key: normalize_space(value) if isinstance(value, str) else value for key, value in record.items()}
    cleaned["phone"] = normalize_phone(cleaned.get("phone"))
    
    # Clean bad company names that ruin cold email personalization
    company_name = (cleaned.get("company_name") or "").strip().lower()
    if company_name in ["self employed", "self-employed", "freelance", "freelancer", "independent contractor"]:
        cleaned["company_name"] = ""

    cleaned["duplicate_key"] = duplicate_key(cleaned)
    cleaned["quality_score"] = quality_score(cleaned)
    return cleaned

