# FORGE — Autonomous CTF Intelligence & Exploitation Framework

FORGE is a local web-based autonomous CTF command center engineered for cybersecurity competitions.

## Architecture & Security Principles
- **WHAT vs HOW vs WHETHER**: 
  - AI Agents decide **WHAT** capability is needed.
  - The Tool Manager resolves **HOW** to execute approved tools.
  - The Privilege Manager decides **WHETHER** elevated/privileged execution is permitted.
- **Provider Abstraction & Cost Protection**: Paid models default to `PAID_MODEL_ALLOWED=false` with budget tracking. Provider fallback ensures high uptime.
- **Resilient Checkpoints**: Investigations state machines survive restarts, target IP changes, and API disconnects.

## Quick Start (Development)

### Backend Setup
```bash
# Create python virtual environment
python -m venv venv
# Linux / Parrot OS: source venv/bin/activate
# Windows: venv\Scripts\activate

# Install dependencies
pip install -r backend/requirements.txt

# Start FastAPI server
python -m uvicorn backend.main:app --reload --port 8000
```

### Frontend Setup
```bash
cd frontend
npm install
npm run dev
```

Access the dashboard at `http://localhost:5173`.
