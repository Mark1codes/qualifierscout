# How to Update Your Local Codebase with Latest Repository Changes

This step-by-step guide explains how team members can safely update their local **QualifierScout** codebase to get the latest features, scraper improvements, and frontend updates.

---

## ⚡ Quick Start: Pull Latest Changes

Open your terminal in the root folder of the project (`qualifierscout`) and run:

```bash
git pull origin main
```

If you have no uncommitted local changes, Git will automatically download and merge all latest files!

---

## 🔄 Step-by-Step Complete Update Workflow

Follow these steps whenever a teammate pushes new code to the repository:

### Step 1: Handle Any Local Uncommitted Work (Optional)

Before pulling, check if you have unsaved local work by running:

```bash
git status
```

* **If your working tree is clean:** Proceed directly to Step 2.
* **If you have local changes you want to save temporarily:**
  ```bash
  git stash
  ```
  *(After pulling in Step 2, run `git stash pop` to bring back your local work).*
* **If you want to discard your local uncommitted changes:**
  ```bash
  git reset --hard HEAD
  ```

---

### Step 2: Fetch and Merge Latest Code

Pull the updated code from the remote `main` branch:

```bash
git pull origin main
```

---

### Step 3: Update Dependencies

Whenever new packages or libraries are added to the project, update both backend and frontend environments:

#### 1. Backend Python Dependencies
Activate your virtual environment and install updated requirements:

**For Windows (PowerShell):**
```powershell
cd backend
.\venv\Scripts\activate
pip install -r requirements.txt
```

**For Mac / Linux:**
```bash
cd backend
source venv/bin/activate
pip install -r requirements.txt
```

#### 2. Frontend Node Dependencies
Navigate to the frontend folder and install updated node packages:

```bash
cd frontend
npm install
```

---

### Step 4: Restart Development Servers

To ensure all new code and endpoints take effect, restart your running servers:

1. **Backend Server:**
   ```bash
   cd backend
   python -m uvicorn app.main:app --reload
   ```

2. **Frontend Server:**
   ```bash
   cd frontend
   npm run dev
   ```

---

## ❓ Frequently Asked Questions & Troubleshooting

### Issue: "Merge Conflict in [file_name]"
If Git reports a merge conflict when pulling:
```bash
# Option A: Keep remote changes completely (overwrites local conflicts)
git fetch origin
git reset --hard origin/main

# Option B: Rebase your local commits cleanly
git pull --rebase origin main
```

### Issue: Backend or Frontend Errors After Pulling
1. Ensure virtual environment is activated (`venv`).
2. Run `pip install -r requirements.txt` in `backend/`.
3. Run `npm install` in `frontend/`.
4. Clear browser cache or hard reload (`Ctrl + Shift + R` or `Cmd + Shift + R`).
