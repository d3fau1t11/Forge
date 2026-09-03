# FORGE — Competition Setup & Deployment Guide

This guide describes how to deploy and operate **FORGE** on **Parrot OS**, Kali Linux, Ubuntu, or Windows 11 without requiring Docker.

## 1. System Requirements & Dependencies

### Operating System
- Parrot OS (Recommended) / Kali Linux / Ubuntu 22.04+ / Windows 11

### Recommended Pre-installed Tools
- `nmap`, `ffuf`, `gobuster`, `curl`, `httpx`, `strings`, `binwalk`, `file`, `unzip`, `7z`
- Python 3.10+ & Node.js 18+

Install system tools on Parrot OS:
```bash
sudo apt update && sudo apt install -y \
  python3 python3-pip python3-venv \
  nodejs npm \
  nmap ffuf gobuster curl binutils binwalk p7zip-full unzip
```

---

## 2. Installation & Environment Configuration

### Step A: Clone & Virtual Environment
```bash
git clone https://github.com/your-org/forge.git
cd forge

# Create Python virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install Python dependencies
pip install -r backend/requirements.txt
```

### Step B: Environment Variables Setup
Copy the example file and populate your API keys:
```bash
cp .env.example .env
```
Edit `.env`:
```env
GEMINI_API_KEY=your_gemini_key
NVIDIA_API_KEY=your_nvidia_key
CEREBRAS_API_KEY=your_cerebras_key
OPENROUTER_API_KEY=your_openrouter_key
HF_TOKEN=your_hf_token
CLOUDFLARE_API_TOKEN=your_cloudflare_token
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id

DATABASE_URL=sqlite:///./forge.db
PAID_MODEL_ALLOWED=true
DAILY_BUDGET_USD=5.00
```

### Step C: Frontend Dependencies Setup
```bash
cd frontend
npm install
cd ..
```

---

## 3. Launching FORGE

### Terminal 1 — Start Backend Server
```bash
source venv/bin/activate
python -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

### Terminal 2 — Start Frontend Dashboard
```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in your browser.

---

## 4. Running End-to-End Competition Self-Test

To verify all 15 subsystems before a competition match:
```bash
python scripts/competition_test.py
```
Expected Output:
```text
FORGE COMPETITION TEST
----------------------------------------
Environment        PASS
Database           PASS
Providers          PASS
Model Router       PASS
Tool Manager       PASS
Privilege          PASS
Target Manager     PASS
Orchestrator       PASS
Recon              PASS
Web                PASS
Evidence           PASS
Verification       PASS
Checkpoint         PASS
Kill Switch        PASS
Reporting          PASS
----------------------------------------
END-TO-END         PASS
```

---

## 5. Emergency Shutdown & Recovery

- **Dashboard Emergency Kill Switch**: Click the red **EMERGENCY KILL SWITCH** button in the header.
- **CLI Kill Switch**: Run `curl -X POST http://127.0.0.1:8000/api/killswitch`
- **Database Backup**: Copy `forge.db` or run PostgreSQL pg_dump.
