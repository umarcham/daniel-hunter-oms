<div align="center">

# 🕶️ Daniel Hunter Eyewear
### Prescription Operations Console

*AI-powered Order Management System for Luxury Eyewear*

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?style=for-the-badge&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Gemini AI](https://img.shields.io/badge/Gemini_2.5_Flash-AI_Powered-4285F4?style=for-the-badge&logo=google&logoColor=white)](https://ai.google.dev/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Cloud_SQL-336791?style=for-the-badge&logo=postgresql&logoColor=white)](https://cloud.google.com/sql)
[![Cloud Run](https://img.shields.io/badge/Google_Cloud_Run-Deployed-4285F4?style=for-the-badge&logo=googlecloud&logoColor=white)](https://cloud.google.com/run)
[![License](https://img.shields.io/badge/License-MIT-gold?style=for-the-badge)](LICENSE)

---

> **A luxury-grade internal operations platform** for tracking prescription eyewear orders from intake to delivery, featuring real-time AI turnaround predictions, a 2D lens inventory matrix, and a virtual time simulator for stress-testing fulfillment pipelines.

</div>

---

## ✨ Features at a Glance

| Module | Capability |
|--------|-----------|
| 📋 **Orders Dashboard** | Live SLA countdowns, stage transitions, QC failure loops, AI TAT predictions |
| 🔬 **Inventory Matrix** | 2D SPH × CYL lens power grid with real-time stock health color coding |
| ⏱️ **Simulation Desk** | Virtual clock to fast-forward time, trigger lab bottlenecks & view cascading SLA effects |
| 🔔 **Alert Feed** | Simulated WhatsApp, SMS & Email breach notifications pushed to operations channels |
| 🤖 **Daniel Hunter AI** | Gemini 2.5 Flash predictions with local caching — zero blocking on the UI thread |
| 🛡️ **Bot Protection** | Per-IP rate limiting (Flask-Limiter) on all write endpoints |
| 🔄 **DB Failover** | Automatic PostgreSQL → SQLite fallback for zero-downtime resilience |

---

## 🖥️ Screenshots

<div align="center">

### Orders & SLA Dashboard
![Dashboard showing active orders, SLA countdowns and AI predictions](https://via.placeholder.com/900x500/0d0d0d/c9a84c?text=Active+Orders+%7C+SLA+Lifecycle+Dashboard)

### Lens Power Inventory Matrix
![2D SPH/CYL inventory grid with color-coded stock levels](https://via.placeholder.com/900x400/0d0d0d/c9a84c?text=In-House+Lens+Power+Inventory+Matrix)

### Simulation Desk & Alerts
![Simulation desk with virtual clock, bottleneck toggles and alert feed](https://via.placeholder.com/900x400/0d0d0d/c9a84c?text=Simulation+Desk+%7C+Operations+Alert+Feed)

</div>

---

## 🏗️ Architecture Overview

```
┌────────────────────────────────────────────────────────┐
│                   Browser (Vanilla JS)                  │
│   Dashboard · Inventory Matrix · Simulation Controls   │
└───────────────────────┬────────────────────────────────┘
                        │ HTTP / REST API
┌───────────────────────▼────────────────────────────────┐
│              Flask Application (app.py)                 │
│  Rate Limiting (Flask-Limiter) · Route Handlers        │
└───────┬───────────────┬────────────────┬───────────────┘
        │               │                │
┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼──────────────┐
│  database.py  │ │ ai_helper.py│ │    simulator.py      │
│  PostgreSQL   │ │ Gemini 2.5  │ │  Virtual Clock &     │
│  ↕ SQLite     │ │ Flash + LRU │ │  Bottleneck Engine   │
│  Failover     │ │ Cache       │ │                      │
└───────┬───────┘ └──────┬──────┘ └──────────────────────┘
        │               │
┌───────▼──────┐  ┌──────▼──────────────────────────────┐
│ Cloud SQL     │  │  generativelanguage.googleapis.com  │
│ PostgreSQL    │  │  (Gemini Developer API)             │
└──────────────┘  └──────────────────────────────────────┘
```

**Deployed on:** Google Cloud Run · **Database:** Cloud SQL (PostgreSQL) with local SQLite fallback

---

## 🚀 Quick Start (Local Development)

### Prerequisites
- Python 3.11+
- `git`

### 1. Clone the repository
```bash
git clone https://github.com/YOUR_USERNAME/daniel-hunter-oms.git
cd daniel-hunter-oms
```

### 2. Create a virtual environment
```bash
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure environment variables
```bash
cp .env.example .env
# Open .env and fill in your values (see Configuration section below)
```

### 5. Run the application
```bash
python3 app.py
```

Open **http://localhost:5001** in your browser. 🎉

> **No database setup required for local development.** If PostgreSQL environment variables are absent or unreachable, the app automatically falls back to a local SQLite database (`eluno.db`).

---

## ⚙️ Configuration

Copy `.env.example` to `.env` and fill in your values:

```env
# PostgreSQL / Google Cloud SQL
DB_HOST=34.xx.xx.xx          # Public IP or /cloudsql/project:region:instance
DB_PORT=5432
DB_NAME=postgres
DB_USER=postgres
DB_PASS=your_password

# Gemini Developer API (get yours at aistudio.google.com)
GEMINI_API_KEY=your_gemini_api_key

# Optional — only needed if using Vertex AI fallback
# GCP_PROJECT_ID=your_gcp_project_id
# GOOGLE_APPLICATION_CREDENTIALS=GOOGLE_APPLICATION_CREDENTIALS.json
```

---

## 🧠 AI Engine — Daniel Hunter AI

The prediction engine uses **Gemini 2.5 Flash** to estimate:
- **Remaining TAT** (Turnaround Time in hours) before delivery
- **SLA Breach Probability** (0–100%) based on current queue state

### How it works

```
Order Created / Stage Updated
         │
         ▼
Instant heuristic prediction assigned   ← Returns to user in <10ms
         │
         ▼  (background thread)
Gemini 2.5 Flash queried with:
  • Current stage & history
  • Lab bottleneck status
  • Sourcing type (in-house vs vendor)
  • Lens complexity & coating type
         │
         ▼
Refined prediction written to DB
         │
         ▼
Frontend smart polling detects update
and refreshes the table row silently
```

### Caching Strategy
Predictions are **cached per order** using a hash of the input state. If the stage, QC count, delay reason, and bottleneck flags haven't changed, the remaining hours are adjusted locally using elapsed time — **zero API calls, zero latency**.

---

## 📦 Module Breakdown

### Module 1 — Orders & SLA Lifecycle Dashboard

| Feature | Details |
|---------|---------|
| **Intake Form** | Customer name, store location, lens type (SV/Bifocal/Progressive), frame model, lens index & coating |
| **Live Rx Validation** | Instantly checks if prescription powers, index & coating are in-house or need external sourcing |
| **SLA Rules** | Single Vision = 48h · Bifocal = 96h · Progressive = 120h |
| **Stage Pipeline** | Order Placed → Lens Sourcing → Lab Processing → Coating → QC Check → Ready → Shipped → Delivered |
| **QC Loop** | QC failure loops the order back to Lab Processing, incrementing the fail counter |
| **KPIs** | Active Orders · SLA Breach Rate · In-House Sourcing % · QC Pass Rate |

### Module 2 — In-House Lens Power Inventory Matrix

A **2D grid** (SPH rows × CYL columns) showing physical lens blank stock:

| Cell Color | Meaning |
|------------|---------|
| 🟢 Green | In-house stock available (≥ 3 units) |
| 🟡 Yellow | Low stock warning (1–2 units) |
| ⬛ Dark | Out-of-house range or depleted |

Click any cell to open the **Inventory Restock** modal and log a vendor intake.

**In-house ranges:** SPH `-6.00` to `+4.00` · CYL `-2.00` to `0.00` · Index `1.56`, `1.61` · Coatings: Standard, Anti-Reflective

### Module 3 — Simulation Desk & Alert Feed

| Control | Effect |
|---------|--------|
| **+6 Hours** | Advances virtual clock by 6 hours |
| **+24 Hours** | Advances virtual clock by 24 hours |
| **Courier & Sourcing Delay** | Flags all sourced orders as delayed |
| **Laboratory Edging Backup** | Extends lab processing estimates |
| **Coating Equipment Capacity** | Extends coating stage estimates |

Bottleneck toggles force the AI to recalculate all active predictions, simulating real-world cascade effects on SLA timelines.

---

## 🛡️ Security & Rate Limiting

All write endpoints are protected with **per-IP rate limiting**:

| Endpoint | Limit |
|----------|-------|
| `POST /api/orders` | 10 / minute |
| `POST /api/orders/<id>/status` | 15 / minute |
| `POST /api/inventory/stock` | 30 / minute |
| `POST /api/simulator/tick` | 30 / minute |
| `POST /api/simulator/bottleneck` | 30 / minute |

Rate-limited requests receive a clean `429` JSON response:
```json
{ "error": "Rate limit exceeded. Please wait a moment before trying again." }
```

---

## ☁️ Deploying to Google Cloud Run

### First-time deploy
```bash
gcloud run deploy daniel-hunter-oms \
    --source . \
    --region us-central1 \
    --allow-unauthenticated \
    --set-env-vars \
        DB_HOST=/cloudsql/PROJECT:REGION:INSTANCE,\
        DB_USER=postgres,\
        DB_PASS=your_password,\
        DB_NAME=postgres,\
        GEMINI_API_KEY=your_key \
    --add-cloudsql-instances PROJECT:REGION:INSTANCE
```

### Updating only the API key (no rebuild needed)
```bash
gcloud run services update daniel-hunter-oms \
    --update-env-vars GEMINI_API_KEY="your_new_key" \
    --region us-central1
```

---

## 🧪 Running Tests

```bash
python3 test_app.py
```

```
Ran 6 tests in 0.037s
OK
```

Test coverage includes:
- ✅ Database initialisation & seeding
- ✅ Order creation (in-house vs sourced routing)
- ✅ Stage transition & QC failure loop
- ✅ Simulator tick & bottleneck toggling
- ✅ Inventory stock allocation (atomic transactions)
- ✅ Gemini AI prediction caching (no duplicate API calls)

---

## 📁 Project Structure

```
daniel-hunter-oms/
├── app.py                   # Flask routes & rate limiting
├── database.py              # PostgreSQL ↔ SQLite adapter & schema
├── ai_helper.py             # Gemini AI integration & prediction cache
├── simulator.py             # Virtual clock & alert engine
├── requirements.txt         # Python dependencies
├── Dockerfile               # Cloud Run container definition
├── .env.example             # Environment variable template
├── architecture_note.md     # Detailed system architecture notes
├── test_app.py              # Unit test suite (6 tests)
├── templates/
│   └── index.html           # Single-page luxury dashboard
└── static/
    ├── css/style.css        # Dark gold luxury theme
    └── js/app.js            # Reactive frontend logic
```

---

## 🤝 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | Python 3.11, Flask 3.x |
| **Frontend** | Vanilla JS, Vanilla CSS (no frameworks) |
| **AI** | Google Gemini 2.5 Flash (Developer API) |
| **Database** | PostgreSQL (Cloud SQL) with SQLite fallback |
| **Deployment** | Google Cloud Run + Docker |
| **Rate Limiting** | Flask-Limiter |
| **Auth/IAM** | Google Service Account (Vertex AI fallback) |

---

<div align="center">

Built with ❤️ for **Daniel Hunter Eyewear** — *Precision. Craftsmanship. Technology.*

</div>
