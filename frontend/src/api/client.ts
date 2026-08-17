import type { Lead, LeadsResponse, ScrapeRun, ScrapeStartPayload, VerificationStatus } from "../types/api";

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...options?.headers },
    ...options,
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export function startScrape(payload: ScrapeStartPayload): Promise<{ run_id: number }> {
  return request("/scrape/start", { method: "POST", body: JSON.stringify(payload) });
}

export function getRun(runId: number): Promise<ScrapeRun> {
  return request(`/scrape/runs/${runId}`);
}

export function getLeads(search: string, status: string): Promise<LeadsResponse> {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (status !== "all") params.set("verification_status", status);
  return request(`/leads?${params.toString()}`);
}

export function updateLead(id: number, verification_status: VerificationStatus): Promise<Lead> {
  return request(`/leads/${id}`, {
    method: "PATCH",
    body: JSON.stringify({ verification_status }),
  });
}

function getFriendlyLicenseName(type: string): string {
  if (!type || type === "MixedTypes") return type;
  const upper = type.toUpperCase();
  if (upper.includes("GB") || upper.includes("B-") || upper.includes("CG")) return "GeneralContractor";
  if (upper.includes("EE") || upper.includes("EC") || upper.includes("ER") || upper.includes("C-10")) return "ElectricalContractor";
  if (upper.includes("MM") || upper.includes("PL") || upper.includes("C-36")) return "PlumbingContractor";
  if (upper.includes("HVAC") || upper.includes("CA") || upper.includes("C-20")) return "HVACContractor";
  if (upper.includes("ROOF") || upper.includes("RC") || upper.includes("C-39") || upper.includes("CCC")) return "RoofingContractor";
  return type.replace(/[^a-zA-Z0-9-]/g, "");
}

export async function exportLeads(format: "csv" | "xlsx", verifiedOnly: boolean, runId?: number, state?: string, city?: string, status?: string, search?: string, licenseType?: string): Promise<void> {
  const response = await fetch(`${API_BASE}/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ format, verified_only: verifiedOnly, run_id: runId, state, city, status, search }),
  });
  if (!response.ok) throw new Error(await response.text());
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  
  const dateStr = new Date().toISOString().split('T')[0];
  const stateStr = state && state !== "all" ? state : "AllStates";
  const cityStr = city && city !== "all" ? `_${city.replace(/[^a-zA-Z0-9-\s]/g, "").replace(/\s+/g, "")}` : "";
  const searchStr = search ? `_${search.replace(/[^a-zA-Z0-9-]/g, "")}` : "";
  const licenseStr = licenseType ? `_${getFriendlyLicenseName(licenseType)}` : "";
  const verifiedStr = verifiedOnly ? "_Verified" : "";
  const filename = `QS_${stateStr}${cityStr}${licenseStr}${searchStr}${verifiedStr}_Leads_${dateStr}.${format}`;
  
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

