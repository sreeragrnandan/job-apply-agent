# ApplyPilot

**Autonomous Job Application Pipeline Powered by AI.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![PyPI version](https://img.shields.io/pypi/v/applypilot?color=blue)](https://pypi.org/project/applypilot/)
[![GitHub stars](https://img.shields.io/github/stars/Pickle-Pixel/ApplyPilot?style=social)](https://github.com/Pickle-Pixel/ApplyPilot)

---

## Overview

ApplyPilot is an autonomous end-to-end job search and application pipeline. It discovers relevant job opportunities across major boards and enterprise portals, evaluates role fit against your profile, tailors your resume and cover letter for each specific position, and autonomously completes and submits the applications in your browser.

---

## Key Features

- **Multi-Source Job Discovery**: Crawls major job boards (LinkedIn, Indeed, Glassdoor, Google Jobs), queries top tech Workday portals (NVIDIA, Salesforce, Adobe, Cisco, Intel, PayPal, Uber) via direct CXS APIs, and scrapes curated direct tech platforms (Instahyre, Wellfound, Cutshort, Razorpay, OpenAI, Amazon Jobs, RemoteOK, Himalayas, WeWorkRemotely) with automated agency filtering and URL deduplication.
- **Automated Description Enrichment**: Extracts full job descriptions using JSON-LD metadata, selector patterns, and AI fallback parsing.
- **AI Match Scoring**: Rates each opportunity (1–10) based on your real experience, skills, and preferences. Only high-fit positions proceed.
- **Per-Job Resume Tailoring**: Adapts your resume to match role requirements, emphasizing relevant accomplishments and keywords without ever fabricating facts.
- **Targeted Cover Letters**: Drafts personalized cover letters mapped directly to the employer's tech stack and mission.
- **Native Browser Auto-Apply**: Powered by a native Gemini agent over Playwright (CDP). Navigates complex ATS forms (Workday, Greenhouse, Lever), uploads documents, fills inputs, and submits hands-free.
- **Adaptive Learnings System**: Continuously learns and remembers employer screening answers, custom question responses, and ATS layout patterns across sessions in `learnings.json`.
- **Live Terminal & Web Dashboard**: Monitor active jobs, application statuses, and worker activity in real time.

---

## The 6-Stage Pipeline

```
┌────────────┐     ┌───────────┐     ┌───────────┐
│ 1.Discover │ ──> │  2.Enrich │ ──> │  3.Score  │
└────────────┘     └───────────┘     └───────────┘
                                           │
┌────────────┐     ┌───────────┐           ▼
│6.Auto-Apply│ <── │ 5.Letters │ <── ┌───────────┐
└────────────┘     └───────────┘     │ 4.Tailor  │
                                     └───────────┘
```

| Stage | Description |
|---|---|
| **1. Discover** | Aggregates listings across major job boards (LinkedIn, Indeed, Glassdoor), top tech Workday portals (NVIDIA, Adobe, Cisco, Salesforce, Intel, PayPal, Uber), and direct tech career platforms (Instahyre, Wellfound, Cutshort, Razorpay, OpenAI, Amazon Jobs, RemoteOK, Himalayas, WeWorkRemotely) with strict product-company filtering and canonical URL deduplication. |
| **2. Enrich** | Extracts full job descriptions, compensation ranges, and location/remote criteria using JSON-LD metadata, CSS selectors, and AI fallback parsing. |
| **3. Score** | Rates role compatibility from 1 to 10 against candidate profile, skills, and preferences. Filters out low-fit opportunities to focus only on top matches. |
| **4. Tailor** | Dynamically reorganizes and optimizes resume achievements and keywords for the target role while strictly preserving factual work history. |
| **5. Cover Letter** | Generates a concise, high-impact cover letter tailored specifically to the target company, hiring team, and role requirements. |
| **6. Auto-Apply** | Autonomous browser agent (Gemini + Playwright): instantly detects expired/closed 404 postings on Turn 1, navigates multi-page ATS forms (Workday, Greenhouse, Lever), uploads resumes, answers screening questions, continuously saves patterns into `learnings.json`, and submits. |

---

## Quick Start

### 1. Installation

```bash
pip install applypilot
pip install --no-deps python-jobspy && pip install pydantic tls-client requests markdownify regex
playwright install chromium
```

> **Note on `python-jobspy`:** Installing with `--no-deps` bypasses strict numpy version locks in upstream metadata and ensures smooth compatibility with modern Python environments.

### 2. Initial Setup

Run the interactive setup wizard to configure your profile, resume, job preferences, and API credentials:

```bash
applypilot init
```

Verify your environment and dependencies:

```bash
applypilot doctor
```

### 3. Running the Pipeline

```bash
# Run discovery, scoring, resume tailoring, and cover letter generation
applypilot run

# Run with 4 parallel discovery workers
applypilot run -w 4

# Autonomous browser application submission
applypilot apply

# Test form completion without clicking final submit
applypilot apply --dry-run

# Apply to a specific job URL directly
applypilot apply --url "https://example.wd5.myworkdayjobs.com/.../job/..."
```

---

## Windows One-Click Scripts (`setup.bat` & `launch.bat`)

For Windows users, ApplyPilot includes two dedicated batch scripts in the root directory for automated installation and dashboard execution without manual terminal management:

### 1. `setup.bat` — Automated Environment Setup
Double-click `setup.bat` (or run `.\setup.bat` from Command Prompt / PowerShell) to run the full setup pipeline:
- **Prerequisite Validation**: Verifies Python 3.11+ and pip are installed. If Node.js is missing, it attempts automatic silent installation via `winget`.
- **Virtual Environment**: Automatically creates an isolated `.venv` virtual environment in the repository root.
- **Dependency Installation**: Installs the ApplyPilot package in editable mode (`pip install -e .`) along with all dependencies (`python-jobspy`, `playwright`, `pydantic`, `tls-client`, etc.).
- **Browser Setup**: Downloads and configures the Playwright Chromium browser binaries.
- **Pre-Config Migration**: If you place an `applypilot_content\` folder containing your existing `.env`, `profile.json`, `resume.pdf`, `resume.txt`, and YAML files next to `setup.bat`, it will automatically copy them to `%USERPROFILE%\.applypilot\`.
- **Diagnostics**: Runs `applypilot doctor` upon completion to verify all tools and API keys are ready.

### 2. `launch.bat` — Web Dashboard Launcher
Double-click `launch.bat` (or run `.\launch.bat` from Command Prompt / PowerShell) to launch the interactive UI:
- **Environment Activation**: Automatically activates the project's `.venv` virtual environment.
- **Dependency Check**: Verifies that Flask is installed, automatically installing it if missing.
- **Local Web Server**: Starts the ApplyPilot web dashboard service at `http://localhost:5000`.
- **Automatic Browser Launch**: Automatically opens your default web browser to the dashboard, providing an interface to review tailored resumes, manage applications, track jobs, and monitor live worker status.
- **Clean Shutdown**: Stop the web dashboard anytime with `Ctrl+C`.

---

## Configuration Files

All configuration files are managed in `~/.applypilot/`:

- **`profile.json`**: Candidate master record including personal contact details, work history, education, skills, salary requirements, and EEO preferences.
- **`searches.yaml`**: Job search queries (Backend, Full Stack, SDE 2, Golang), location criteria (Bengaluru, Remote), and strict product-company filters blocking 70+ IT staffing agencies and consultancies.
- **`employers.yaml`**: Workday direct employer registry for top global & India tech companies (NVIDIA, Salesforce, Adobe, Cisco, Intel, PayPal, Mastercard, Uber, DocuSign, Workday).
- **`sites.yaml`**: Direct tech career boards and ATS portals (Instahyre, Wellfound India, Cutshort, Razorpay, OpenAI via Ashby, Amazon Jobs, RemoteOK, Himalayas, WeWorkRemotely).
- **`.env`**: API credentials (such as `GEMINI_API_KEY`) and execution options.
- **`learnings.json`**: Dynamic memory of employer screening answers, Workday field selectors, and custom compliance responses that improve speed and accuracy over time.

---

## CLI Reference

| Command | Description |
|---|---|
| `applypilot init` | Run the guided setup wizard |
| `applypilot doctor` | Diagnose environment, installed browsers, and API keys |
| `applypilot run` | Execute discovery, enrichment, scoring, and tailoring |
| `applypilot run -w 4` | Run discovery with 4 parallel threads |
| `applypilot run --min-score 8` | Process only jobs scoring 8 or higher |
| `applypilot apply` | Launch browser auto-apply engine |
| `applypilot apply -w 3` | Launch 3 parallel browser workers |
| `applypilot apply --dry-run` | Fill all fields and inspect without final submission |
| `applypilot apply --headless` | Run browser automation in headless mode |
| `applypilot apply --url <URL>` | Run auto-apply on a single target job URL |
| `applypilot status` | View pipeline metrics and application tallies |
| `applypilot dashboard` | Launch the local web dashboard for inspection |

---

## Contributing

Contributions, bug reports, and suggestions are welcome! Feel free to open an issue or submit a pull request on GitHub.
