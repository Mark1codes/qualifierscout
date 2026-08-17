export type VerificationStatus = "verified" | "needs_review" | "not_verified" | "rejected";

export interface Lead {
  id: number;
  run_id: number;
  contractor_name: string;
  company_name: string;
  license_number: string;
  license_type: string;
  license_status: string;
  expiration_date: string;
  address: string;
  city: string;
  state: string;
  zip_code: string;
  phone: string;
  email: string;
  website: string;
  source_url: string;
  verification_status: VerificationStatus;
  quality_score: number;
  duplicate_count: number;
  notes: string;
  date_scraped: string;
}

export interface LeadStats {
  total: number;
  unique_leads: number;
  duplicates: number;
  verified: number;
  needs_review: number;
  not_verified: number;
}

export interface LeadsResponse {
  leads: Lead[];
  stats: LeadStats;
}

export interface ScrapeRun {
  id: number;
  state: string;
  license_type: string;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  total_records: number;
  unique_leads: number;
  duplicate_leads: number;
  verified_leads: number;
  logs: Array<{ message: string; level: string; created_at: string }>;
}

export interface ScrapeStartPayload {
  state: string;
  license_type: string;
  city?: string;
  license_status: string;
  max_records: number;
  enrich_leads: boolean;
}

