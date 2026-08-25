# QualifierScout

QualifierScout is a professional B2B lead generation and data triangulation platform specifically designed for scraping, verifying, and enriching contractor license data from various state portals.

## 🚀 Features
- **Multi-State Scraping Engines:** Native support for scraping contractor license portals in Florida, California, North Carolina, Texas, New Mexico, Nevada, Alaska, and Arizona.
- **Dynamic Trade Mapping:** State-specific mapping to pull exact equivalents (e.g., HVAC companies in Texas where General Contractor licenses don't exist).
- **Ghost Hunter Enrichment:** Automatically enriches missing contact info and social media profiles using Apollo and LinkedIn integrations.
- **Analytics Dashboard:** Beautiful, real-time charts tracking lead quality and geographic distribution using Recharts.
- **Local Settings:** Customize default export formats and max records per scrape natively in the browser.
- **Exporting:** One-click exports of verified leads to CSV or Excel (`.xlsx`).

---

## 🛠 Prerequisites
Before running the application on your own machine, ensure you have the following installed:
- **VScode** (Download VScode)(https://code.visualstudio.com/download?_exp_download=d53503e735) YT Tutorial: https://www.youtube.com/watch?v=DA03DODTP5w 
- **Git** ([Download Git](https://git-scm.com/downloads)) YT Tutorial: https://www.youtube.com/watch?v=FWDPqistXd4
- **Python 3.10+** ([Download Python](https://www.python.org/downloads/))  YT Tutorial:youtube.com/watch?si=ARMUMrvOlnFqU7ro&v=UN38d21cbBg&feature=youtu.be
- **Node.js 18+** ([Download Node.js](https://nodejs.org/))  YT Tutorial:https://www.youtube.com/watch?v=-9baVMhbHPg

---

## 💻 Installation & Setup

First, clone the repository to your local machine:
```bash
git clone https://github.com/Mark1codes/qualifierscout.git
cd qualifierscout
```

### 1. Backend Setup (FastAPI / Python)

Open a new terminal window and navigate to the backend folder:
```bash
cd backend
```

**For Windows:**
```powershell
# Create a virtual environment
python -m venv venv

# Activate the virtual environment
.\venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

**For Mac / Linux:**
```bash
# Create a virtual environment
python3 -m venv venv

# Activate the virtual environment
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

**Environment Variables:**
Create a `.env` file in the `backend/` directory and add your API keys:
```env
APOLLO_API_KEY=your_apollo_key_here
```

**Run the Backend Server:**
```bash
python -m uvicorn app.main:app --reload
```
*The backend will now be running on `http://localhost:8000`.*

---

### 2. Frontend Setup (React / Vite)

Open a second terminal window and navigate to the frontend folder:
```bash
cd frontend
```

**For both Windows and Mac:**
```bash
# Install Node dependencies
npm install

# Start the frontend development server
npm run dev
```
*The frontend will typically be running on `http://localhost:5173`. Open this URL in your browser to view the application.*

---

## ⚙️ How to Use
1. Open the **Scraper** tab.
2. Select your target **State**, **Trade / License Type**, and **City**.
3. Choose the maximum number of records to pull.
4. Toggle **Enable Ghost Hunter** if you want automatic Apollo/LinkedIn enrichment.
5. Click **Start Scrape** and monitor the live progress log.
6. Once completed, navigate to the **Exports** tab to download your leads as CSV or Excel.

## 📝 License
Proprietary software for QualifierScout internal operations.
