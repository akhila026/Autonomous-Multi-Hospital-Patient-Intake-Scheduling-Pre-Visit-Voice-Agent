# AegisCare AI — Healthcare Platform

A resilient healthcare AI platform featuring autonomous clinical voice triage, specialist discovery, and **Two-Phase Resilient Mock EHR Synchronization** with idempotent timeout recovery, dynamic pre-visit questionnaires, automated reminders, and multi-tenant admin dashboards.

---

## Architecture & Features

1. **Hospital Onboarding & Lifecycle**: Self-service hospital tenant registration (`DRAFT` → `SUBMITTED` → `APPROVED` / `REJECTED`).
2. **Doctor Scheduling & Calendars**: Tiered availability, recurring weekly schedules, discrete 30-minute time slots, and surgical blocking.
3. **Patient Management & Portal**: Self-registration, patient ownership isolation, intake questionnaires, appointment histories, and preferences.
4. **Autonomous AI Patient-Access Agent**: Conversational administrative assistant with strict clinical guardrails (escalates chest pain / red flags immediately).
5. **Two-Phase Resilient Mock EHR Synchronization**: Standalone or in-process EHR simulation with chaos injection, 504 Gateway Timeout simulation, idempotent verification query (`GET /mock-ehr/verify`), and automatic reconciliation.
6. **Pre-Visit Questionnaires & Physician Reviews**: Specialty-specific question trees with real-time urgency detection and doctor clinical review.
7. **Multi-Tenant Dashboards**: Role-based access control (RBAC) across Patients, Doctors, Hospital Admins, and Platform Super Admins.

---

## Prerequisites

- **Python**: Python 3.10, 3.11, or 3.12
- **Package Manager**: `pip`
- **Git**: For version control and deployment

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone <your-repository-url>
   cd healthcare-ai-platform
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate

   # Windows
   python -m venv .venv
   .venv\Scripts\activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

---

## Environment Variables

Copy `.env.example` to `.env` for local customization:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8000` | Port the application server binds to (provided by Render automatically). |
| `HOST` | `0.0.0.0` | Host address to bind (use `0.0.0.0` for container / cloud deployments). |
| `DATABASE_URL` | `sqlite:///./healthcare.db` | SQLAlchemy database URL. For Render with a persistent disk: `sqlite:////var/data/healthcare.db`. |
| `JWT_SECRET_KEY` | `aegiscare-jwt-secret-key-production-strength-2026-v1` | Secret key used for signing JWT authentication tokens. Set a secure random string in production. |
| `JWT_ALGORITHM` | `HS256` | JWT signing algorithm. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` | JWT token lifespan (default 24 hours). |
| `ALLOWED_ORIGINS` | `*` | Comma-separated CORS allowed origins (e.g. `https://your-service.onrender.com`). |
| `GEMINI_API_KEY` | `""` | Google Gemini API key for dynamic conversational AI (deterministic clinical fallback activates when unset). |
| `MOCK_EHR_BASE_URL` | `""` | Base URL for standalone Mock EHR service. If unset/empty, the platform automatically utilizes its high-performance in-process ASGI engine. |
| `MOCK_EHR_TIMEOUT_SEC` | `3.0` | EHR request timeout threshold. |
| `SLOT_HOLD_DURATION_MINUTES` | `5` | Hold window duration before unconfirmed appointment slots are released. |
| `APPLICATION_TIMEZONE` | `UTC` | Timezone used for appointment scheduling calculations. |

---

## Database Setup & Behavior

- **Clean Deployment**: Database schema is automatically initialized (`Base.metadata.create_all`) upon application startup.
- **Idempotent Seed Data**: Seed demo hospitals, doctors, time slots, questionnaires, and users are automatically populated on initial run. On subsequent restarts, the seeding routine verifies existing data and does **not** duplicate records.
- **Persistence Consideration**: By default, the application uses SQLite (`healthcare.db`). In free/standard cloud environments without persistent storage, ephemeral container disks reset on each deploy or server restart. To persist SQLite data between restarts on Render, attach a **Render Persistent Disk** mounted at `/var/data` and configure `DATABASE_URL=sqlite:////var/data/healthcare.db`.

---

## Local Startup

Run the application using the local startup runner:
```bash
python run.py
```
Or start directly with Uvicorn:
```bash
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

- Web Application: **http://localhost:8000**
- Health Check: **http://localhost:8000/health**
- Swagger API Docs: **http://localhost:8000/docs**

---

## Production Startup

The platform is designed to be started with standard production commands:
```bash
uvicorn backend.main:app --host 0.0.0.0 --port $PORT
```
In Windows PowerShell (local testing):
```powershell
$env:PORT="8000"
uvicorn backend.main:app --host 0.0.0.0 --port $env:PORT
```

---

## Render Deployment Guide

Follow these exact steps to deploy to Render:

1. **Push your code to GitHub**:
   Ensure you push all code to your GitHub repository (secrets, local databases, and caches are already ignored by `.gitignore`).

2. **Log in to Render**:
   Open [Render Dashboard](https://dashboard.render.com).

3. **Create a New Web Service**:
   - Click **New +** → **Web Service**.
   - Connect your GitHub repository: `healthcare-ai-platform`.

4. **Configure Web Service Settings**:
   - **Name**: `healthcare-ai-platform` (or your preferred name)
   - **Region**: Select your preferred region (e.g. `Oregon (US West)` or `Frankfurt (EU)`)
   - **Branch**: `main`
   - **Root Directory**: Leave blank (repository root)
   - **Runtime**: `Python 3`
   - **Build Command**:
     ```bash
     pip install -r requirements.txt
     ```
   - **Start Command**:
     ```bash
     uvicorn backend.main:app --host 0.0.0.0 --port $PORT
     ```
   - **Instance Type**: `Free` (or higher)

5. **Configure Environment Variables**:
   Under **Advanced** → **Environment Variables**, add:
   - `PYTHON_VERSION`: `3.11.9`
   - `JWT_SECRET_KEY`: `<generate-a-secure-random-string>`
   - `ALLOWED_ORIGINS`: `*` (or your Render URL: `https://<your-app-name>.onrender.com`)
   - `APPLICATION_TIMEZONE`: `UTC`
   - *(Optional)* `GEMINI_API_KEY`: `<your-gemini-api-key>` (if using live Gemini AI)

6. **Configure Health Check Path**:
   Under **Advanced** → **Health Check Path**, set:
   ```
   /health
   ```

7. **(Optional) Database Persistence on Render**:
   - If using a paid instance with Render Disks:
     - Add a Disk mounted at `/var/data` (e.g., 1 GB).
     - Add environment variable `DATABASE_URL=sqlite:////var/data/healthcare.db`.
   - On the Free tier without disks, SQLite operates on the ephemeral container disk (demo seed data re-initializes cleanly if container restarts).

8. **Deploy**:
   Click **Create Web Service**. Render will automatically clone the repo, run `pip install -r requirements.txt`, bind to `$PORT`, and bring the service live.

---

## Test Execution

Run the complete automated test suite (191 tests):
```bash
python -m unittest discover -s backend/tests -p "test_*.py"
```

Run the production-style end-to-end readiness verification script:
```bash
python scratch/verify_production_readiness.py
```

---

## Default Demo Credentials

| Role | Email | Password |
| :--- | :--- | :--- |
| **Patient** | `alice.morgan@example.com` | `PatientPass123!` |
| **Doctor** | `sarah.jenkins@stjudehealth.org` | `DoctorPass123!` |
| **Doctor (Metro)** | `marcus.vance@metrogeneral.org` | `DoctorPass123!` |
| **Hospital Admin** | `david.miller@stjudehealth.org` | `AdminPass123!` |
| **Platform Admin** | `platform.admin@aegiscare.io` | `PlatformAdmin123!` |
