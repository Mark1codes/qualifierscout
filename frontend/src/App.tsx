import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  Columns3,
  Database,
  Download,
  FileSpreadsheet,
  Upload,
  LayoutDashboard,
  ListFilter,
  Loader2,
  Menu,
  Play,
  Radar,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Mail,
  Phone as PhoneIcon,
  Linkedin,
} from "lucide-react";
import { exportLeads, getLeads, getRun, startScrape, updateLead } from "./api/client";
import type { Lead, LeadStats, ScrapeRun, VerificationStatus } from "./types/api";
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend
} from "recharts";

const emptyStats: LeadStats = {
  total: 0,
  unique_leads: 0,
  duplicates: 0,
  verified: 0,
  needs_review: 0,
  not_verified: 0,
};

const CITIES_BY_STATE: Record<string, string[]> = {
  "Florida": ["Miami", "Orlando", "Tampa", "Jacksonville", "Tallahassee", "Fort Lauderdale", "St. Petersburg", "Hialeah", "Port St. Lucie", "Cape Coral", "Pembroke Pines", "Hollywood", "Miramar", "Gainesville", "Coral Springs", "Miami Gardens", "Clearwater", "Palm Bay", "Pompano Beach", "West Palm Beach", "Lakeland", "Davie", "Miami Beach", "Sunrise", "Plantation", "Boca Raton", "Deltona", "Palm Coast", "Largo", "Deerfield Beach", "Melbourne", "Boynton Beach", "Lauderhill", "Weston", "Fort Myers", "Kissimmee", "Homestead", "Tamarac", "Delray Beach", "Daytona Beach", "Wellington", "Jupiter", "North Miami", "Port Orange", "Coconut Creek", "Ocala", "Sanford", "Margate", "Sarasota", "Pensacola"],
  "California": ["Los Angeles", "San Francisco", "San Diego", "Sacramento", "San Jose", "Fresno", "Long Beach", "Oakland", "Bakersfield", "Anaheim", "Santa Ana", "Riverside", "Stockton", "Chula Vista", "Irvine", "Fremont", "San Bernardino", "Modesto", "Fontana", "Santa Clarita", "Glendale", "Huntington Beach", "Santa Rosa", "Oceanside", "Rancho Cucamonga", "Ontario", "Lancaster", "Elk Grove", "Palmdale", "Corona", "Salinas", "Pomona", "Torrance", "Hayward", "Escondido", "Sunnyvale", "Pasadena", "Orange", "Fullerton", "Thousand Oaks", "Visalia", "Simi Valley", "Concord", "Roseville", "Victorville", "Santa Clara", "Vallejo", "Berkeley", "Downey", "Costa Mesa"],
  "North Carolina": ["Charlotte", "Raleigh", "Greensboro", "Durham", "Winston-Salem", "Fayetteville", "Cary", "Wilmington", "High Point", "Concord", "Asheville", "Greenville", "Gastonia", "Jacksonville", "Apex", "Huntersville", "Chapel Hill", "Burlington", "Kannapolis", "Rocky Mount", "Mooresville", "Wake Forest", "Wilson", "Hickory", "Holly Springs", "Indian Trail", "Fuquay-Varina", "Salisbury", "Monroe", "Goldsboro", "Garner", "New Bern", "Sanford", "Matthews", "Cornelius", "Thomasville", "Asheboro", "Statesville", "Mint Hill", "Kernersville", "Leland", "Shelby", "Clemmons", "Lexington", "Elizabeth City", "Carrboro", "Lenoir", "Boone", "Mount Airy", "Kinston"],
  "Georgia": ["Atlanta", "Augusta", "Savannah", "Columbus", "Macon", "Athens", "Sandy Springs", "South Fulton", "Roswell", "Johns Creek", "Warner Robins", "Albany", "Alpharetta", "Marietta", "Stonecrest", "Smyrna", "Valdosta", "Brookhaven", "Dunwoody", "Newnan", "Gainesville", "Peachtree Corners", "Mableton", "Milton", "Peachtree City", "Douglasville", "Rome", "East Point", "Tucker", "Woodstock", "Hinesville", "Canton", "Statesboro", "Dalton", "Martinez", "Duluth", "Redan", "Evans", "Covington", "Sugar Hill", "Griffin", "Decatur", "Pooler", "Carrollton", "Acworth", "Cartersville", "Suwanee", "Perry", "Snellville", "Thomasville"],
  "Texas": ["Houston", "Austin", "Dallas", "San Antonio", "Fort Worth", "El Paso", "Arlington", "Corpus Christi", "Plano", "Laredo", "Lubbock", "Garland", "Irving", "Amarillo", "Grand Prairie", "Brownsville", "McKinney", "Frisco", "Pasadena", "Mesquite", "Killeen", "McAllen", "Carrollton", "Midland", "Waco", "Denton", "Abilene", "Odessa", "Beaumont", "Round Rock", "The Woodlands", "Richardson", "Pearland", "College Station", "Wichita Falls", "Lewisville", "Tyler", "San Angelo", "League City", "Allen", "Sugar Land", "Edinburg", "Mission", "Longview", "Bryan", "Baytown", "Pharr", "Temple", "Missouri City", "Flower Mound"],
  "New Mexico": ["Albuquerque", "Las Cruces", "Rio Rancho", "Santa Fe", "Roswell", "Farmington", "Clovis", "Hobbs", "Alamogordo", "Carlsbad", "Gallup", "Los Lunas", "Sunland Park", "Deming", "Artesia", "Las Vegas", "Portales", "Silver City", "Taos", "Grants", "Ruidoso", "Socorro", "Espanola", "Lovington", "Belen", "Bernalillo", "Corrales", "Bloomfield", "Aztec", "Truth or Consequences", "Los Alamos", "Raton", "Edgewood", "Milan", "Anthony", "Santa Rosa", "Eunice", "Tucumcari", "Tularosa", "Jal", "Mesilla", "Dexter", "Bayard", "Chaparral", "Santa Teresa", "Vado", "Zuni Pueblo", "Kirtland", "Moriarty", "Estancia", "Peralta", "Bosque Farms", "Hatch", "Lordsburg", "Magdalena", "Angel Fire", "Eagle Nest", "Red River", "Pecos", "Jemez Springs", "Mescalero", "Vaughn", "Carrizozo", "Tijeras"],
  "Nevada": ["Las Vegas", "Reno", "Henderson", "North Las Vegas", "Sparks", "Carson City", "Elko", "Boulder City", "Mesquite", "Fallon", "Fernley", "Pahrump", "Incline Village"],
  "Alaska": ["Anchorage", "Fairbanks", "Juneau", "Wasilla", "Ketchikan", "Sitka", "Kenai", "Palmer", "Soldotna", "Kodiak", "Barrow"],
  "Arizona": ["Phoenix", "Tucson", "Mesa", "Glendale", "Buckeye", "Benson", "Bouse", "Cottonwood", "Peoria", "Snowflake", "Kingman", "Fredonia", "Queen Creek", "Yuma", "Willcox", "Vail", "Dewey", "Chino Valley", "Casa Grande", "Cornville", "Litchfield Park", "Springerville", "Gold Canyon", "Payson", "Show Low", "New River", "Camp Verde", "Saint Johns", "Morristown", "Chandler", "Gilbert", "Scottsdale", "Tempe", "Surprise", "Goodyear", "Avondale", "Flagstaff", "Lake Havasu City", "Apache Junction", "Maricopa", "Oro Valley", "Prescott", "Bullhead City", "Prescott Valley", "Marana", "Sierra Vista"],
  "Utah": ["Salt Lake City", "West Valley City", "Provo", "West Jordan", "Orem", "Sandy", "Ogden", "St. George", "Layton", "South Jordan", "Lehi", "Millcreek", "Taylorsville", "Logan", "Murray", "Draper", "Bountiful", "Riverton", "Spanish Fork", "Roy", "Pleasant Grove", "Kearns", "Tooele", "Cottonwood Heights", "Midvale", "Springville", "Eagle Mountain", "Cedar City", "American Fork", "Kaysville"],
  "Colorado": ["Denver", "Colorado Springs", "Aurora", "Fort Collins", "Lakewood", "Thornton", "Arvada", "Westminster", "Pueblo", "Greeley", "Centennial", "Boulder", "Highlands Ranch", "Longmont", "Loveland", "Broomfield", "Castle Rock", "Commerce City", "Parker", "Littleton"],
};

const ALL_LICENSE_TYPES = [
  "Underground Contractor", "General Contractor", "Railroad and Underground", "Residential Contractor",
  "Building Contractor", "Electrical Contractor", "Plumbing Contractor",
  "HVAC Contractor", "Roofing Contractor", "Mechanical Contractor",
  "Pool Contractor", "Masonry Contractor", "Concrete Contractor",
  "Drywall Contractor", "Painting Contractor", "Carpentry Contractor",
  "Framing Contractor", "Flooring Contractor", "Insulation Contractor",
  "Tile and Stone Contractor", "Landscaping Contractor", "Excavation Contractor",
  "Paving Contractor", "Asphalt Contractor", "Fencing Contractor",
  "Glazing Contractor", "Siding Contractor", "Demolition Contractor",
  "Structural Steel Contractor", "Elevator Contractor", "Fire Protection Contractor",
  "Security Alarm Contractor", "Low Voltage Contractor", "Solar Contractor",
  "Well Drilling Contractor", "Septic Tank Contractor", "Asbestos Abatement",
  "Lead Abatement", "Mold Remediation", "Water Damage Restoration",
];


const LICENSE_TYPES_BY_STATE: Record<string, string[]> = {
  Colorado: [
    "Electrical Contractor",
    "Plumbing Contractor",
    "General Contractor",
    "Building Contractor",
    "Residential Contractor",
  ],
  Utah: [
    "General Contractor",
    "Underground Contractor",
    "Building Contractor",
    "Residential Contractor",
    "Electrical Contractor",
    "HVAC Contractor",
    "Plumbing Contractor",
    "Roofing Contractor",
  ],
  Alaska: [
    "Underground Contractor",
    "General Contractor",
    "Building Contractor",
    "Residential Contractor",
    "Roofing Contractor",
    "Electrical Contractor",
    "HVAC Contractor",
    "Plumbing Contractor",
  ],
  Nevada: [
    "General Contractor",
    "Building Contractor",
    "Residential Contractor",
    "General Engineering",
    "Electrical Contractor",
    "HVAC Contractor",
    "Plumbing Contractor",
    "Roofing Contractor",
  ],
  Texas: [
    "Plumbing Contractor",
    "HVAC Contractor",
    "Electrical Contractor",
    "Elevator Contractor",
    "Mold Remediation Contractor",
    "Water Well Driller",
  ],
  California: [
    "General Contractor",
    "Building Contractor",
    "Residential Contractor",
    "General Engineering",
    "Electrical Contractor",
    "HVAC Contractor",
    "Plumbing Contractor",
    "Roofing Contractor",
  ],
  Florida: [
    "General Contractor",
    "Building Contractor",
    "Residential Contractor",
    "Roofing Contractor",
    "HVAC Contractor",
    "Plumbing Contractor",
  ],
  Arizona: [
    "A-4 Drilling",
    "Well Drilling Contractor",
    "General Contractor",
    "Electrical Contractor",
    "Plumbing Contractor",
    "HVAC Contractor",
    "Roofing Contractor",
    "Solar Contractor",
  ]
};

function getLicenseTypes(state: string): string[] {
  return LICENSE_TYPES_BY_STATE[state] ?? ALL_LICENSE_TYPES;
}

export function App() {
  const [settings, setSettings] = useState(() => {
    const saved = localStorage.getItem('qs_settings');
    return saved ? JSON.parse(saved) : {
      defaultExportFormat: 'csv',
      defaultMaxRecords: 50,
      enableGhostHunterDefault: true,
    };
  });

  const [activeTab, setActiveTab] = useState("Scraper");
  const [state, setState] = useState("North Carolina");
  const [licenseType, setLicenseType] = useState("General Contractor");
  const [city, setCity] = useState("Charlotte");
  const [licenseStatus, setLicenseStatus] = useState("Active");
  const [maxRecords, setMaxRecords] = useState<number | "">(settings.defaultMaxRecords);
  const [enrichLeads, setEnrichLeads] = useState(settings.enableGhostHunterDefault);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [filterState, setFilterState] = useState("all");
  const [filterCity, setFilterCity] = useState("all");
  const [runId, setRunId] = useState<number | null>(null);
  const [run, setRun] = useState<ScrapeRun | null>(null);
  const [leads, setLeads] = useState<Lead[]>([]);
  const [stats, setStats] = useState<LeadStats>(emptyStats);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("Ready to collect contractor license leads.");

  const [alerts, setAlerts] = useState<{ message: string, type: string, time: Date }[]>([]);
  const [showAlerts, setShowAlerts] = useState(false);
  const [unreadAlerts, setUnreadAlerts] = useState(0);
  const [exportVerifiedOnly, setExportVerifiedOnly] = useState(true);
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  async function refreshLeads() {
    const response = await getLeads("", "all");
    setLeads(response.leads);
    setStats(response.stats);
  }

  useEffect(() => {
    refreshLeads().catch(() => setMessage("Backend is offline. Start FastAPI to load leads."));
  }, []);

  useEffect(() => {
    if (!runId) return;
    const timer = window.setInterval(async () => {
      const nextRun = await getRun(runId);
      setRun(nextRun);
      if (nextRun.status === "completed" || nextRun.status === "failed") {
        await refreshLeads();
        window.clearInterval(timer);

        setAlerts(prev => [{
          message: nextRun.status === "completed"
            ? `Scrape #${nextRun.id} completed! Processed ${nextRun.total_records} records.`
            : `Scrape #${nextRun.id} failed to complete.`,
          type: nextRun.status,
          time: new Date()
        }, ...prev]);
        setUnreadAlerts(prev => prev + 1);

        if (Notification.permission === "granted") {
          new Notification(nextRun.status === "completed" ? "Scrape Completed" : "Scrape Failed", {
            body: `Scrape #${nextRun.id} finished.`
          });
        }
      }
    }, 1200);
    return () => window.clearInterval(timer);
  }, [runId]);

  async function handleStartScrape() {
    if (Notification.permission === "default") {
      Notification.requestPermission();
    }
    setLoading(true);
    setMessage("Starting scraper run...");
    try {
      const response = await startScrape({
        state,
        license_type: licenseType,
        city,
        license_status: licenseStatus,
        max_records: typeof maxRecords === "number" ? maxRecords : 50,
        enrich_leads: enrichLeads,
      });
      setRunId(response.run_id);
      setMessage(`Scrape run #${response.run_id} started.`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not start scraper.");
    } finally {
      setLoading(false);
    }
  }

  async function handleImportCSV(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) return;

    setLoading(true);
    setMessage(`Importing bulk leads from ${file.name}...`);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const res = await fetch("http://localhost:8000/leads/import-csv", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        throw new Error("Failed to import CSV file");
      }
      const data = await res.json();
      setMessage(`Successfully imported ${data.imported_count} leads from ${file.name}!`);
      await refreshLeads();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : "Error importing CSV");
    } finally {
      setLoading(false);
      event.target.value = "";
    }
  }

  async function handleStatusChange(id: number, nextStatus: VerificationStatus) {
    const updated = await updateLead(id, nextStatus);
    setLeads((current) => current.map((lead) => (lead.id === id ? updated : lead)));
    await refreshLeads();
  }

  const exportReady = useMemo(() => leads.filter((lead) => lead.verification_status === "verified").length, [leads]);

  // Chart data derived from real leads
  const leadsByState = useMemo(() => {
    const map: Record<string, number> = {};
    leads.forEach((l) => { if (l.state) map[l.state] = (map[l.state] || 0) + 1; });
    return Object.entries(map).map(([state, count]) => ({ state: state.length > 12 ? state.slice(0, 10) + '…' : state, count })).sort((a, b) => b.count - a.count).slice(0, 8);
  }, [leads]);

  const statusDonut = useMemo(() => [
    { name: 'Verified',     value: stats.verified,     color: '#06D1D4' },
    { name: 'Needs Review', value: stats.needs_review, color: '#F59E0B' },
    { name: 'Duplicates',   value: stats.duplicates,   color: '#6366F1' },
    { name: 'Unreviewed',   value: stats.not_verified, color: '#334155' },
  ].filter(d => d.value > 0), [stats]);

  const qualityTrend = useMemo(() => {
    if (leads.length === 0) return [];
    const bucketSize = Math.max(1, Math.ceil(leads.length / 10));
    const buckets: { batch: string; verified: number; review: number; unreviewed: number }[] = [];
    for (let i = 0; i < leads.length; i += bucketSize) {
      const chunk = leads.slice(i, i + bucketSize);
      buckets.push({
        batch:      `#${i + 1}`,
        verified:   chunk.filter(l => l.verification_status === 'verified').length,
        review:     chunk.filter(l => l.verification_status === 'needs_review').length,
        unreviewed: chunk.filter(l => l.verification_status === 'not_verified').length,
      });
    }
    return buckets;
  }, [leads]);

  return (
    <div className={`app-shell ${isSidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <svg height="29" width="23" viewBox="0 0 31 40" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
            <g transform="translate(0, 0) rotate(0 15 20)" id="logogram" style={{ opacity: 1 }}>
              <path fill="#06D1D4" d="M15 0L20.4545 5.33333L0 25.3333V14.6667L15 0Z"/>
              <path fill="#260AF5" d="M2.90827 28.177L15 40L30 25.3334V14.6667L20.4545 5.33337L0 25.3334L0.0041688 25.3375L20.4545 5.33337V20.6667L11.25 29.6667V20.1324L2.90827 28.177Z"/>
            </g>
          </svg>
          </div>
          <div className="brand-copy">
            <strong>Qualifier<span>Scout</span></strong>
            <small>Lead intelligence</small>
          </div>
        </div>
        <nav className="nav">
          <p className="nav-label">Workspace</p>
          {[
            ["Dashboard", LayoutDashboard],
            ["Scraper", Database],
            ["Leads", FileSpreadsheet],
            ["Deduplication", Columns3],
            ["Verification", ShieldCheck],
            ["Settings", Settings],
          ].map(([label, Icon]) => (
            <button
              className={activeTab === label ? "active" : ""}
              key={label as string}
              onClick={() => setActiveTab(label as string)}
            >
              <span className="nav-icon"><Icon size={18} /></span>
              <span>{label as string}</span>
              {label === "Verification" && <span className="count">{stats.needs_review}</span>}
            </button>
          ))}
        </nav>
        <div className="plan-box">
          <div className="plan-box-topline"><span className="plan-status" /> Local workspace</div>
          <strong>Contractor records</strong>
          <div className="meter"><span style={{ width: `${Math.min(100, stats.total * 2)}%` }} /></div>
          <p><b>{stats.total}</b> leads stored locally</p>
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button" onClick={() => setIsSidebarCollapsed(!isSidebarCollapsed)}><Menu size={19} /></button>
            <div>
              <h1>{activeTab}</h1>
              <p>{message}</p>
            </div>
          </div>
          <div className="topbar-actions" style={{ position: 'relative' }}>
            <button className="ghost-button" onClick={() => { setShowAlerts(!showAlerts); setUnreadAlerts(0); }}>
              <Bell size={17} /> Alerts {unreadAlerts > 0 && <span className="alert-badge">{unreadAlerts}</span>}
            </button>
            {showAlerts && (
              <div className="alerts-dropdown">
                <h4>Recent Alerts</h4>
                {alerts.length === 0 ? <p className="no-alerts">No alerts yet</p> : (
                  alerts.map((alert, i) => (
                    <div key={i} className={`alert-item ${alert.type}`}>
                      <p>{alert.message}</p>
                      <small>{alert.time.toLocaleTimeString()}</small>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </header>

        <div className="tab-content" style={{ display: "flex", flexDirection: "column", gap: "1.5rem" }}>
          {activeTab === "Dashboard" && (
            <>
              {/* ── Stat strip ── */}
              <section className="stats-strip">
                <Stat label="Records Found" value={stats.total} />
                <Stat label="Unique Leads" value={stats.unique_leads} />
                <Stat label="Duplicates" value={stats.duplicates} />
                <Stat label="Verified" value={stats.verified} />
                <Stat label="Needs Review" value={stats.needs_review} />
                <Stat label="Export Ready" value={exportReady} />
              </section>

              {/* ── Analytics row ── */}
              <section style={{ display: 'grid', gridTemplateColumns: '1fr 360px', gap: '1.25rem' }}>

                {/* Bar chart — leads by state */}
                <div className="panel" style={{ padding: '1.5rem' }}>
                  <div style={{ marginBottom: '1rem' }}>
                    <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Leads by State</h3>
                    <p style={{ margin: '2px 0 0', fontSize: '1.5rem', fontWeight: 700, color: '#F1F5F9' }}>{leads.length} <span style={{ fontSize: '0.9rem', fontWeight: 400, color: '#64748B' }}>total</span></p>
                  </div>
                  {leads.length === 0 ? (
                    <div style={{ height: 220, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#475569', fontSize: '0.85rem' }}>Run the scraper to populate data</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={220}>
                      <BarChart data={leadsByState} margin={{ top: 4, right: 4, left: -20, bottom: 0 }} barCategoryGap="30%">
                        <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                        <XAxis dataKey="state" tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
                        <YAxis tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
                        <Tooltip
                          contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }}
                          labelStyle={{ color: '#F1F5F9', fontWeight: 600 }}
                          itemStyle={{ color: '#06D1D4' }}
                          cursor={{ fill: 'rgba(99,102,241,0.08)' }}
                        />
                        <Bar dataKey="count" name="Leads" fill="#6366F1" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  )}
                </div>

                {/* Donut chart — status breakdown */}
                <div className="panel" style={{ padding: '1.5rem' }}>
                  <div style={{ marginBottom: '1rem' }}>
                    <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Status Breakdown</h3>
                    <p style={{ margin: '2px 0 0', fontSize: '1.5rem', fontWeight: 700, color: '#F1F5F9' }}>
                      {stats.total > 0 ? `${Math.round((stats.verified / stats.total) * 100)}%` : '—'}
                      <span style={{ fontSize: '0.9rem', fontWeight: 400, color: '#64748B' }}> verified</span>
                    </p>
                  </div>
                  {statusDonut.length === 0 ? (
                    <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#475569', fontSize: '0.85rem' }}>No data yet</div>
                  ) : (
                    <ResponsiveContainer width="100%" height={180}>
                      <PieChart>
                        <Pie data={statusDonut} cx="50%" cy="50%" innerRadius={52} outerRadius={78} paddingAngle={3} dataKey="value" strokeWidth={0}>
                          {statusDonut.map((entry, i) => <Cell key={i} fill={entry.color} />)}
                        </Pie>
                        <Tooltip
                          contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }}
                          itemStyle={{ color: '#F1F5F9' }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                  )}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem', marginTop: '0.5rem' }}>
                    {statusDonut.map((d, i) => (
                      <div key={i} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '0.8rem' }}>
                        <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', color: '#94A3B8' }}>
                          <span style={{ width: 8, height: 8, borderRadius: '50%', background: d.color, display: 'inline-block' }} />
                          {d.name}
                        </span>
                        <span style={{ color: '#F1F5F9', fontWeight: 600 }}>{d.value}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </section>

              {/* ── Quality trend area chart ── */}
              <div className="panel" style={{ padding: '1.5rem' }}>
                <div style={{ marginBottom: '1rem' }}>
                  <h3 style={{ margin: 0, fontSize: '0.85rem', fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Lead Quality Trend</h3>
                  <p style={{ margin: '2px 0 0', fontSize: '0.8rem', color: '#475569' }}>Verified vs review vs duplicates across batches</p>
                </div>
                {qualityTrend.length === 0 ? (
                  <div style={{ height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#475569', fontSize: '0.85rem' }}>Run the scraper to see quality trends</div>
                ) : (
                  <ResponsiveContainer width="100%" height={180}>
                    <AreaChart data={qualityTrend} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
                      <defs>
                        <linearGradient id="gVerified" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#06D1D4" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#06D1D4" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="gReview" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#F59E0B" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#F59E0B" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="gDupes" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="5%" stopColor="#6366F1" stopOpacity={0.3} />
                          <stop offset="95%" stopColor="#6366F1" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" stroke="#1E293B" vertical={false} />
                      <XAxis dataKey="batch" tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fill: '#64748B', fontSize: 11 }} axisLine={false} tickLine={false} />
                      <Tooltip
                        contentStyle={{ background: '#0F172A', border: '1px solid #1E293B', borderRadius: 8, fontSize: 12 }}
                        labelStyle={{ color: '#F1F5F9', fontWeight: 600 }}
                        cursor={{ stroke: '#334155' }}
                      />
                      <Legend wrapperStyle={{ fontSize: 12, color: '#64748B', paddingTop: 8 }} />
                      <Area type="monotone" dataKey="verified"   name="Verified"     stroke="#06D1D4" strokeWidth={2} fill="url(#gVerified)" dot={false} />
                      <Area type="monotone" dataKey="review"     name="Needs Review" stroke="#F59E0B" strokeWidth={2} fill="url(#gReview)"   dot={false} />
                      <Area type="monotone" dataKey="unreviewed" name="Unreviewed"   stroke="#6366F1" strokeWidth={2} fill="url(#gDupes)"    dot={false} />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </div>

              {/* ── Quality panel ── */}
              <section className="main-grid" style={{ gridTemplateColumns: '1fr' }}>
                <aside className="panel quality-panel">
                  <PanelTitle title="Review & Quality" />
                  <QualityLine label="Verified" value={stats.verified} tone="green" />
                  <QualityLine label="Needs Review" value={stats.needs_review} tone="amber" />
                  <QualityLine label="Duplicates" value={stats.duplicates} tone="blue" />
                </aside>
              </section>
            </>
          )}

          {activeTab === "Scraper" && (
            <>
              <section className="main-grid">
                <section className="panel settings-panel">
                  <PanelTitle title="Scrape Settings" />
                  <Field label="State">
                    <select
                      value={state}
                      onChange={(event) => {
                        const newState = event.target.value;
                        setState(newState);
                        setCity(CITIES_BY_STATE[newState]?.[0] || "");
                        // Reset license type to first valid option for new state
                        const types = getLicenseTypes(newState);
                        setLicenseType(types[0]);
                      }}
                    >
                      {Object.keys(CITIES_BY_STATE)
                        .filter((st) => !["Georgia", "Utah", "Colorado"].includes(st))
                        .map((st) => (
                          <option key={st}>{st}</option>
                        ))}
                    </select>
                  </Field>
                  <Field label="Trade / License Type">
                    <select value={licenseType} onChange={(event) => setLicenseType(event.target.value)}>
                      {getLicenseTypes(state).map((t) => (
                        <option key={t}>{t}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="City">
                    <select value={city} onChange={(event) => setCity(event.target.value)}>
                      <option value="">All Cities (Statewide)</option>
                      {(CITIES_BY_STATE[state] || []).map((c) => (
                        <option key={c} value={c}>{c}</option>
                      ))}
                    </select>
                  </Field>
                  <Field label="License Status">
                    <select value={licenseStatus} onChange={(event) => setLicenseStatus(event.target.value)}>
                      <option value="Active">Active Only</option>
                      <option value="Inactive">Inactive Only</option>
                      <option value="All">All Statuses (Active + Inactive)</option>
                    </select>
                  </Field>
                  <Field label="Max Records">
                    <input
                      type="number"
                      min={1}
                      max={5000}
                      value={maxRecords}
                      onChange={(event) => {
                        const val = event.target.value;
                        setMaxRecords(val === "" ? "" : Number(val));
                      }}
                    />
                  </Field>
                  <Field label="Enable Lead Enrichment & Verification (Apollo + ZeroBounce)">
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '6px' }}>
                      <input
                        type="checkbox"
                        checked={enrichLeads}
                        onChange={(event) => setEnrichLeads(event.target.checked)}
                        style={{ width: '18px', height: '18px', cursor: 'pointer' }}
                      />
                      <span style={{ fontSize: '0.9rem', color: 'var(--slate-400)' }}>
                        Automatically find contact info & verify email deliverability
                      </span>
                    </div>
                  </Field>
                  <button className="primary-button" onClick={handleStartScrape} disabled={loading}>
                    {loading ? <Loader2 className="spin" size={18} /> : <Play size={18} />}
                    Start Scrape
                  </button>
                  {/* 
                  <div style={{ marginTop: '12px' }}>
                    <label className="secondary-button" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', cursor: 'pointer', padding: '10px', borderRadius: '6px', border: '1px solid var(--slate-700)', background: 'var(--slate-800)', color: 'var(--slate-200)', fontSize: '0.9rem' }}>
                      <Upload size={16} />
                      Import State CSV/Excel (Free)
                      <input type="file" accept=".csv,.xlsx" onChange={handleImportCSV} style={{ display: 'none' }} />
                    </label>
                  </div>
                  */}
                </section>

                <section className="panel log-panel">
                  <div className="panel-header">
                    <PanelTitle title="Live Scrape Progress" />
                    <span className={`run-status ${run?.status ?? "idle"}`}>{run?.status ?? "Idle"}</span>
                  </div>
                  <div className="terminal">
                    {(run?.logs.length ? run.logs : [{ created_at: "", level: "info", message: "Waiting for the next scraper run..." }]).map(
                      (log, index) => (
                        <div className={log.level} key={`${log.created_at}-${index}`}>
                          <span>{log.created_at ? new Date(log.created_at).toLocaleTimeString() : "--:--:--"}</span>
                          <p>{log.message}</p>
                        </div>
                      )
                    )}
                  </div>
                  <div className="progress-row">
                    <span>Progress</span>
                    <div className="progress"><span style={{ width: `${run?.progress ?? 0}%` }} /></div>
                    <strong>{run?.progress ?? 0}%</strong>
                  </div>
                </section>
              </section>

              {run?.status === "completed" && (
                <section className="panel table-panel" style={{ marginTop: "1.5rem" }}>
                  <div className="table-toolbar">
                    <div>
                      <h2>Scrape Results</h2>
                      <p>
                        {leads.filter(l => l.run_id === run?.id).length} new unique leads found.
                        {leads.filter(l => l.run_id === run?.id).length === 0 && " (All other leads were skipped as duplicates)."}
                      </p>
                    </div>
                    <div className="table-actions">
                      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', cursor: 'pointer', color: '#344255' }}>
                        <input type="checkbox" checked={exportVerifiedOnly} onChange={(e) => setExportVerifiedOnly(e.target.checked)} />
                        Verified Only
                      </label>
                      <button onClick={() => {
                        const runLeads = leads.filter(l => l.run_id === run?.id);
                        const types = new Set(runLeads.map(l => l.license_type).filter(Boolean));
                        const runLicenseType = types.size === 1 ? Array.from(types)[0] : types.size > 1 ? "MixedTypes" : undefined;
                        exportLeads("csv", exportVerifiedOnly, run?.id, state, undefined, undefined, undefined, runLicenseType);
                      }} className="primary-button" style={{ padding: "0.5rem 1rem", display: "flex", alignItems: "center", gap: "0.5rem", borderRadius: "6px" }}><Download size={16} /> Export CSV</button>
                    </div>
                  </div>
                  <LeadTable leads={leads.filter(l => l.run_id === run?.id)} onStatusChange={handleStatusChange} />
                </section>
              )}
            </>
          )}

          {(activeTab === "Leads" || activeTab === "Verification" || activeTab === "Deduplication") && (() => {
            let filteredLeads = leads;

            // 1. Tab base filtering
            if (activeTab === "Verification") {
              filteredLeads = filteredLeads.filter(l => l.verification_status === "needs_review");
            } else if (activeTab === "Deduplication") {
              filteredLeads = filteredLeads.filter(l => l.duplicate_count > 0);
            } else if (activeTab === "Leads" && statusFilter !== "all") {
              filteredLeads = filteredLeads.filter(l => l.verification_status === statusFilter);
            }

            // 2. State & City filtering
            if (filterState !== "all") {
              filteredLeads = filteredLeads.filter(l => l.state === filterState);
            }
            if (filterCity !== "all") {
              filteredLeads = filteredLeads.filter(l => l.city === filterCity);
            }

            // 3. Text search
            if (search.trim()) {
              const query = search.toLowerCase();
              filteredLeads = filteredLeads.filter(l =>
                (l.company_name || "").toLowerCase().includes(query) ||
                (l.contractor_name || "").toLowerCase().includes(query) ||
                (l.license_number || "").toLowerCase().includes(query) ||
                (l.email || "").toLowerCase().includes(query)
              );
            }

            // Get available states and cities for the dropdowns
            const availableStates = Array.from(new Set(leads.map(l => l.state).filter(Boolean))).sort();
            const availableCities = filterState !== "all"
              ? Array.from(new Set(leads.filter(l => l.state === filterState).map(l => l.city).filter(Boolean))).sort()
              : Array.from(new Set(leads.map(l => l.city).filter(Boolean))).sort();

            return (
              <section className="panel table-panel">
                <div className="table-toolbar">
                  <div>
                    <h2>{activeTab}</h2>
                    <p>{filteredLeads.length} visible records</p>
                  </div>
                  <div className="table-actions">
                    <div className="search-box">
                      <Search size={16} />
                      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search name, company, license..." />
                    </div>
                    {activeTab === "Leads" && (
                      <>
                        <button onClick={() => {
                          const types = new Set(filteredLeads.map(l => l.license_type).filter(Boolean));
                          const expLicenseType = types.size === 1 ? Array.from(types)[0] : types.size > 1 ? "MixedTypes" : undefined;
                          exportLeads("csv", false, undefined, filterState !== "all" ? filterState : undefined, filterCity !== "all" ? filterCity : undefined, statusFilter !== "all" ? statusFilter : undefined, search.trim() ? search : undefined, expLicenseType);
                        }} className="ghost-button" style={{ padding: "0 10px", border: "1px solid #d4dde5", background: "#fff", display: "flex", gap: "6px", height: "38px", color: "#344255" }}>
                          <Download size={15} /> Export
                        </button>
                        <select value={filterState} onChange={(event) => { setFilterState(event.target.value); setFilterCity("all"); }}>
                          <option value="all">All States</option>
                          {availableStates.map(s => <option key={s} value={s}>{s}</option>)}
                        </select>
                        <select value={filterCity} onChange={(event) => setFilterCity(event.target.value)} disabled={availableCities.length === 0}>
                          <option value="all">All Cities</option>
                          {availableCities.map(c => <option key={c} value={c}>{c}</option>)}
                        </select>
                        <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
                          <option value="all">All statuses</option>
                          <option value="verified">Verified</option>
                          <option value="needs_review">Needs review</option>
                          <option value="not_verified">Not verified</option>
                          <option value="rejected">Rejected</option>
                        </select>
                      </>
                    )}
                    <button className="ghost-button"><SlidersHorizontal size={16} /> Filters</button>
                  </div>
                </div>
                <LeadTable leads={filteredLeads} onStatusChange={handleStatusChange} />
              </section>
            );
          })()}

          {activeTab === "Settings" && (
            <section className="main-grid" style={{ gridTemplateColumns: '1fr 1fr', gap: '2rem' }}>
              <div className="panel settings-panel">
                <PanelTitle title="Application Preferences" />
                <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', marginTop: '1rem' }}>
                  <Field label="Default Export Format">
                    <select 
                      value={settings.defaultExportFormat} 
                      onChange={(e) => {
                        const newSettings = {...settings, defaultExportFormat: e.target.value};
                        setSettings(newSettings);
                        localStorage.setItem('qs_settings', JSON.stringify(newSettings));
                      }}
                    >
                      <option value="csv">CSV Document (.csv)</option>
                      <option value="xlsx">Excel Workbook (.xlsx)</option>
                    </select>
                  </Field>
                  <Field label="Default Max Records">
                    <input 
                      type="number" 
                      value={settings.defaultMaxRecords} 
                      onChange={(e) => {
                        const newSettings = {...settings, defaultMaxRecords: parseInt(e.target.value) || 50};
                        setSettings(newSettings);
                        localStorage.setItem('qs_settings', JSON.stringify(newSettings));
                        setMaxRecords(newSettings.defaultMaxRecords);
                      }} 
                    />
                  </Field>
                  <label className="checkbox-label" style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer' }}>
                    <input 
                      type="checkbox" 
                      checked={settings.enableGhostHunterDefault} 
                      onChange={(e) => {
                        const newSettings = {...settings, enableGhostHunterDefault: e.target.checked};
                        setSettings(newSettings);
                        localStorage.setItem('qs_settings', JSON.stringify(newSettings));
                        setEnrichLeads(newSettings.enableGhostHunterDefault);
                      }}
                      style={{ accentColor: '#06D1D4', width: '18px', height: '18px' }}
                    />
                    <span style={{ fontSize: '0.9rem', color: '#E2E8F0', fontWeight: 500 }}>Enable Ghost Hunter by Default</span>
                  </label>
                </div>
              </div>

              <div className="panel" style={{ marginTop: '24px' }}>
                <PanelTitle title="API Credit Usage & Tracking" />
                <p className="panel-desc" style={{ color: '#94A3B8', fontSize: '0.85rem', marginBottom: '16px' }}>Real-time breakdown of API credit consumption for Apollo.io and ZeroBounce.</p>

                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '16px' }}>
                  <div style={{ background: '#0F172A', border: '1px solid #1E293B', padding: '16px', borderRadius: '8px' }}>
                    <small style={{ color: '#94A3B8', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>APOLLO CREDITS USED</small>
                    <strong style={{ display: 'block', fontSize: '1.6rem', color: '#06D1D4', marginTop: '6px' }}>
                      {stats.apollo_credits_total ?? 0}
                    </strong>
                    <span style={{ fontSize: '0.75rem', color: '#64748B' }}>Verified contacts unlocked</span>
                  </div>

                  <div style={{ background: '#0F172A', border: '1px solid #1E293B', padding: '16px', borderRadius: '8px' }}>
                    <small style={{ color: '#94A3B8', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>ZEROBOUNCE CREDITS USED</small>
                    <strong style={{ display: 'block', fontSize: '1.6rem', color: '#10B981', marginTop: '6px' }}>
                      {stats.zerobounce_credits_total ?? 0}
                    </strong>
                    <span style={{ fontSize: '0.75rem', color: '#64748B' }}>Emails verified for deliverability</span>
                  </div>

                  <div style={{ background: '#0F172A', border: '1px solid #1E293B', padding: '16px', borderRadius: '8px' }}>
                    <small style={{ color: '#94A3B8', fontSize: '0.75rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.05em' }}>APOLLO SEARCH REQUESTS</small>
                    <strong style={{ display: 'block', fontSize: '1.6rem', color: '#3B82F6', marginTop: '6px' }}>
                      {stats.apollo_requests_total ?? 0}
                    </strong>
                    <span style={{ fontSize: '0.75rem', color: '#64748B' }}>Total search queries sent</span>
                  </div>
                </div>
              </div>

            </section>
          )}

        </div>
      </main>
    </div>
  );
}

function PanelTitle({ title }: { title: string }) {
  return <h2 className="panel-title">{title}</h2>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <small>{label}</small>
      <strong>{value}</strong>
    </div>
  );
}

function QualityLine({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="quality-line">
      <span className={`dot ${tone}`} />
      <p>{label}</p>
      <strong>{value}</strong>
    </div>
  );
}

function LeadTable({ leads, onStatusChange }: { leads: Lead[]; onStatusChange: (id: number, status: VerificationStatus) => void }) {
  if (!leads.length) {
    return (
      <div className="empty-state">
        <Database size={28} />
        <strong>No leads yet</strong>
        <p>No leads match your current filters.</p>
      </div>
    );
  }

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Contractor</th>
            <th>Contact Info</th>
            <th>Location</th>
            <th>License #</th>
            <th>Status</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {leads.map((lead) => (
            <tr key={lead.id}>
              <td>
                <div className="entity-cell">
                  <strong>{lead.company_name || lead.contractor_name || "Unknown"}</strong>
                  {lead.company_name && lead.contractor_name && lead.contractor_name !== lead.company_name && (
                    <span>{lead.contractor_name}</span>
                  )}
                </div>
              </td>
              <td>
                <div className="contact-cell">
                  {lead.email ? (
                    <a href={`mailto:${lead.email}`} className="contact-link" title={lead.email}>
                      <Mail size={14} /> {lead.email.length > 20 ? lead.email.substring(0, 18) + '...' : lead.email}
                    </a>
                  ) : <span className="contact-missing"><Mail size={14} /> --</span>}

                  <div className="contact-row">
                    {lead.phone ? (
                      <span className="contact-text"><PhoneIcon size={14} /> {lead.phone}</span>
                    ) : <span className="contact-missing"><PhoneIcon size={14} /> --</span>}

                    {(lead as any).linkedin ? (
                      <a href={(lead as any).linkedin} target="_blank" rel="noreferrer" className="contact-linkedin">
                        <Linkedin size={14} />
                      </a>
                    ) : null}
                  </div>
                </div>
              </td>
              <td>
                <div className="location-cell">
                  <span>{lead.city || "--"}</span>
                  <small>{lead.state || "--"}</small>
                </div>
              </td>
              <td>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  <a href={lead.source_url} target="_blank" rel="noreferrer" className="license-link" style={{ width: 'fit-content' }}>
                    {lead.license_number}
                  </a>
                  {lead.license_type && (
                    <span style={{ fontSize: '11px', color: '#637181', whiteSpace: 'nowrap', textOverflow: 'ellipsis', overflow: 'hidden', maxWidth: '160px' }} title={lead.license_type}>
                      {lead.license_type}
                    </span>
                  )}
                </div>
              </td>
              <td>
                <div className="status-cell">
                  <span className={`badge ${lead.license_status?.toLowerCase() || 'unknown'}`}>
                    {lead.license_status || "Unknown"}
                  </span>
                  <select
                    className={`status-select ${lead.verification_status}`}
                    value={lead.verification_status}
                    onChange={(event) => onStatusChange(lead.id, event.target.value as VerificationStatus)}
                  >
                    <option value="verified">Verified</option>
                    <option value="needs_review">Needs review</option>
                    <option value="not_verified">Not verified</option>
                    <option value="rejected">Rejected</option>
                  </select>
                </div>
              </td>
              <td>
                <div className="quality-cell">
                  <span className="score"><CheckCircle2 size={14} /> {lead.quality_score || 0}</span>
                  {lead.duplicate_count > 0 && (
                    <span className="dupe" title={`${lead.duplicate_count} duplicates skipped`}><AlertTriangle size={14} /> {lead.duplicate_count}</span>
                  )}
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

