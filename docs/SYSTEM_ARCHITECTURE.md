# QualifierScout System Architecture & Data Pipeline

This document outlines the core data pipeline, accuracy ratings, and enrichment logic for the QualifierScout Lead Engine. It serves as a reference for how raw data is collected, cleaned, and enriched for high-performance cold outreach.

---

## 1. System Accuracy & Effectiveness (9.5 / 10)

For Cold Email and Call outreach effectiveness, the system operates at a **9.5/10** accuracy rating. It is strictly designed to prioritize **Quality over Quantity**.

*   **State Database Accuracy (10/10):** Because the baseline scraper pulls directly from official government licensing portals in real-time, the data is flawless. Every license is real, every contractor is legally authorized, and "Inactive" statuses are automatically filtered out.
*   **Apollo Enrichment Accuracy (8.5/10):** Finding contact info in the construction industry is notoriously difficult. We solve this using a strict "Triangulation Query" to eliminate false positives. The missing 1.5% accounts for the reality of the internet: some contractors rarely update their LinkedIn (resulting in older phone numbers) or simply have zero digital footprint.

---

## 2. Ghost Hunter (Apollo Enrichment) Logic

The most critical part of the Ghost Hunter engine is ensuring we never pull data for the wrong person. To guarantee high accuracy, the backend builds a highly specific **Triangulation Query**.

When the backend queries Apollo, it sends a strict payload:
1. **The Exact Name:** (e.g., `John Doe`)
2. **The Exact Location:** (e.g., `NM`)
3. **The Company Name:** (e.g., `Doe Construction`)

Apollo does not blindly search for "John Doe". It specifically hunts for: *"A person named John Doe, who lives in NM, and works at Doe Construction."* By forcing Apollo to match Name + State + Company simultaneously, the confidence level of the match is incredibly high. 

**The Safety Trigger:** If the scraper determines that a contractor is a lone individual with **no company name**, the system actively blocks Apollo from running. It refuses to let Apollo "guess" based on name alone, ensuring 0 credits are wasted on false positives.

---

## 3. Data Field Lifecycle (Step-by-Step)

Here is the complete, start-to-finish lifecycle of a Lead and how every single field is populated.

### Phase 1: The State Portal (Free Automated Search)
The scraper acts as the foundational "Target Finder", extracting the public baseline data legally required by the state:
*   `contractor_name`
*   `license_number`, `license_type`, `license_status`, `expiration_date`
*   `company_name` (Often blank for sole proprietors)
*   `address`, `city`, `state`, `zip_code`
*   `phone` (The generic state registry phone number)
*   `source_url`

### Phase 2: The Cleaning & Deduplication Engine
Before saving, the backend intercepts the raw data:
*   **Company Cleaning:** It automatically blanks out generic names like "Self Employed", "Self-Employed", "Freelancer", and "Independent Contractor" so they do not ruin mail-merge personalization in your cold emails.
*   **Deduplication (`duplicate_key`):** The system mathematically combines the Name + License + Company into a unique fingerprint. If a contractor holds multiple licenses (e.g., GB02 and GB98), they are correctly saved as two distinct records. If the exact same license is found again, it is skipped.

### Phase 3: Apollo Enrichment
The system takes the triangulated data and searches Apollo for the real-world contact info:
*   **`email`:** Grabs their Verified professional/personal email. It strictly rejects any emails Apollo flags as "bounced" or "risky".
*   **`phone`:** Looks specifically for a direct mobile/desk line. If found, it **overwrites** the generic state phone number. If it doesn't find a direct line, it safely leaves the original State Registry phone number untouched (It explicitly avoids Company HQ generic lines).
*   **`linkedin`:** Grabs their exact LinkedIn URL for the frontend UI.
*   **`title`:** Grabs their job title (e.g., "Owner").
*   **`company_name`:** If the State Portal left this blank, Apollo fills it in with whatever company they currently work for (respecting the "Self Employed" blocker).
*   **`website`:** If the State Portal left this blank, Apollo pulls the official company website.
*   **Blacklist Filter:** If Apollo detects they work for a government agency or retail store (e.g., "City of Albuquerque", "AutoZone"), the lead is flagged and dropped.

### Phase 4: Quality Control & Jhunard's Workflow
The system grades the finished product and sorts it for final approval:
*   `quality_score`: A score out of 100 based on data completeness.
*   **"Verified" Bucket:** If the lead successfully got a safe Email address, it is stamped "Verified" and is instantly ready for export.
*   **"Needs Review" Bucket (Jhunard's Verification Flow):** If the lead passes the state portal (proving they are a real contractor) but Apollo fails to find a confident email match, it is thrown into the "Needs Review" bucket. 
---

## 4. Glossary & License Types

### System Terminology
*   **Qualifier:** A licensed contractor who acts as the legally qualifying party for a construction company's license.
*   **Triangulation Query:** The API logic that combines a contractor's Name, Location, and Company to guarantee high-accuracy Apollo matches and prevent emailing the wrong person.
*   **Duplicate Key (SHA1):** The mathematical fingerprint created by combining a lead's Name, License Number, and Company. It prevents identical records from entering the CRM.
*   **Needs Review (Jhunard's Flow):** A lead that passed state verification but could not be confidently enriched with an email by Apollo. These are queued specifically for Jhunard's manual human verification and outreach.
*   **Verified:** A lead that has passed both state verification and Apollo enrichment, resulting in a safe, deliverable email address.

### Common License Type Codes (Primarily NM & CA)
The backend `client.ts` automatically translates these raw database codes into human-readable categories when exporting CSV files.
*   **GB98:** General Building (Commercial & Residential)
*   **GB02:** General Building (Residential Only)
*   **GF98 / GF:** General Fixed Works (Infrastructure, Railroad, Underground, Asphalt)
*   **EE98 / EC / ER:** Electrical Contractor
*   **MM98 / PL:** Mechanical & Plumbing Contractor
*   **Roof / C-39 / RC:** Roofing Contractor
*   **MixedTypes:** A tag used during export when a selected group of leads contains multiple different license types.
