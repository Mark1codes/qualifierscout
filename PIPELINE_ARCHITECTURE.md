# QualifierScout System Architecture & Pipeline

This document explains the end-to-end data flow of the QualifierScout lead generation engine, detailing how state scraping modules securely extract data and how it is routed into Apollo for B2B enrichment.

---

## High-Level Architecture Overview

QualifierScout operates on an asynchronous Python (FastAPI) backend and a React (Vite) frontend. The core value of the application is a fully automated two-step pipeline:
1. **Extraction:** Navigate state-specific government licensing portals to extract raw contractor data (Names, Company Names, License Numbers).
2. **Enrichment:** Use the extracted company and contractor names to automatically "Triangulate" contact data (Emails, Phone Numbers, LinkedIn Profiles) via Apollo.io.

---

## 1. The Extraction Phase (State Scrapers)

When a user initiates a scrape from the frontend, `main.py` dispatches the task in the background to the appropriate State Scraper in `app/scrapers/`.

### How the Scrapers Work:
Each state has completely different security postures and portal structures. Our scrapers are custom-built to handle these nuances:

*   **API / Open Data:** States with modern infrastructure are queried directly via HTTP requests (e.g., Socrata endpoints) for maximum speed.
*   **Playwright / Headless Automation:** States that require form submissions or complex navigation are handled by Playwright, driving a hidden Chromium browser to navigate the site like a human.
*   **Cloudflare Bypass (ZenRows):** For highly protected states like **Georgia**, the engine routes requests through ZenRows Premium Proxies. The backend sends Javascript instructions to the ZenRows API, which uses residential IPs to solve Turnstile challenges natively, execute the ASP.NET search forms, and return the raw HTML results table.

### Data Normalization:
The scrapers extract the raw HTML/JSON and normalize it into a standard schema (List of Dictionaries). 
Crucially, **the scrapers are engineered to isolate the `company_name`**. If the scraper only returns a person's name (e.g., *John Smith*) without a business entity (e.g., *Smith Construction LLC*), Apollo cannot reliably find B2B contact information.

### State-by-State Scraper & Apollo Workflows:
Each state has a tailored extraction method to maximize Apollo triangulation success:

1. **Texas (TDLR)**
   * **Extraction:** Targets `AIRREF` (Air Conditioning & Refrigeration) company-level licenses instead of generic individual contractor licenses.
   * **Apollo Sync:** Uses Regex to extract both the `contractor_name` and the `company_name` from the state's list view. Apollo takes this `company_name` as the absolute anchor to guarantee accurate B2B enrichment.
2. **Georgia (SOS)**
   * **Extraction:** Bypasses enterprise Cloudflare Turnstile using **ZenRows Premium Proxies** and JS Instructions to render ASP.NET forms securely.
   * **Apollo Sync:** Pulls names from the `datagrid_results`. If a company name is embedded or missing in the list view, Apollo falls back to querying the individual's name within the specific city to triangulate the B2B organization.
3. **Florida (DBPR)**
   * **Extraction:** Uses `httpx.AsyncClient` to maintain strict ASP.NET session cookies and tokens across multiple rapid POST requests to navigate the search portal.
   * **Apollo Sync:** The DBPR portal explicitly returns a `business_name`. The scraper maps this directly to QualifierScout's `company_name` schema, allowing Apollo to instantly find the correct Organization ID.
4. **North Carolina (NCLBG)**
   * **Extraction:** Scrapes the NCLBG licensing portal, handling its specific HTML structure.
   * **Apollo Sync:** Built to strictly separate the business entity from the personal licensee name during parsing. This clean separation ensures Apollo doesn't fail due to mixed "Person / Company" strings.
5. **New Mexico (RLD)**
   * **Extraction:** Headless browser automation (Playwright) navigating the state portal.
   * **Apollo Sync (Ghost Hunter):** New Mexico records often lack clear company entities. Before sending data to Apollo, the pipeline triggers a preliminary LinkedIn Search to find the individual's current "Employer", which is then injected as the `company_name` for Apollo to successfully triangulate.
6. **California (CSLB)**
   * **Extraction:** Playwright browser automation navigating CSLB's Online Services portal (`cslb.ca.gov`). Submits search queries via city/zip code mappings, capturing active general and specialty contractors alongside company contact details.
   * **Apollo Sync:** Provides clear business and sole-proprietorship entity names, mapping directly to Apollo's B2B search parameters with high success rates.
7. **Nevada (NSCB)**
   * **Extraction:** Playwright browser automation navigating the Nevada State Contractors Board portal (`app.nvcontractorsboard.com`). For corporate entity records, the scraper executes deep detail-page navigation to extract individual Officers and Qualifiers (e.g., *Mohammad Hisham Khaleel*).
   * **Apollo Sync:** Provides both individual Principal/Qualifier names and corporate entity names, allowing Apollo to accurately match high-level decision-makers and retrieve direct work emails and phones.

---

## 2. The Smart Filtering Phase

Before sending data to the expensive Apollo API, `main.py` runs the raw leads through a "Smart Filter":
1. **Validation Filter:** Drops junk records that lack both a name and a license number.
2. **Status Filter:** Drops licenses that don't match the requested status (e.g., filtering out "Expired" or "Suspended" licenses).
3. **Database Pre-Filter:** Checks the local SQLite database (`app.db.database`) and drops leads that have already been scraped in the past, saving API credits and preventing duplicates.

---

## 3. The Apollo Enrichment Phase (Triangulation)

If the user enabled "Enrich Leads" in the UI, the filtered records are passed to `app.services.apollo_enrichment.py`. This is where the "Triangulation" happens.

### How Apollo B2B Triangulation Works:
1.  **Organization Search:** The engine calls the Apollo API using the `company_name` (and sometimes the `city` or website) to find the exact B2B entity in Apollo's database.
2.  **Organization ID Extraction:** If Apollo finds a match, it returns a unique `organization_id`.
3.  **Contact Search:** The engine then does a second query to Apollo, asking for contacts *specifically working at that `organization_id`*. 
    *   It will attempt to match the `contractor_name` (the license holder) directly.
    *   If the exact person isn't found, it often falls back to finding high-level decision-makers (Owners, Presidents, CEOs) at that specific company.
4.  **Data Appending:** The engine pulls down the enriched `email`, `phone`, `title`, and `linkedin_url` and appends them to the raw state license record.

*(Special Note: For states like **New Mexico** where company names are frequently missing from state records, the engine uses a "Ghost Hunter" pipeline. It runs a pre-enrichment search using a custom Google Custom Search JSON API to scrape LinkedIn profiles based on the individual's name + city, finding their current Employer before sending it to Apollo.)*

---

## 4. Finalization & Export

1.  **Cleaning:** The enriched records run through `cleaner.py` to strip out extra whitespace, fix capitalization (Title Case), and standardize phone number formatting.
2.  **Storage:** The records are inserted into the local SQLite database (`leads` table) with a `verification_status` flag (Verified if quality is high and email is present, Needs Review otherwise).
3.  **UI Sync:** The frontend polls for progress, and once complete, displays the enriched data in the React dashboard.
4.  **Export:** Users can export the clean, enriched B2B leads to `.csv` or `.xlsx` using the Export button.
