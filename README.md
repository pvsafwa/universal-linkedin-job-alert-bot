# Universal LinkedIn Job Alert Bot

A self-contained, autonomous LinkedIn Job Alert Bot daemon designed to be deployed across any infrastructure with **zero Python code modifications**. 

Every operational parameter, title whitelist/blacklist, seniority trap, regional geofence, required skill validator, tech badge tagger, and WhatsApp forwarding template is **100% controlled via `.env`**.

---

## Key Capabilities

* **Zero External Python Dependencies**: Built entirely on the Python 3 standard library (`urllib`, `sqlite3`, `re`, `json`, `hashlib`).
* **Minimal Footprint**: Runs on `python:3.11-alpine` as a hardened non-root user (`UID 10001`), consuming under 25 MB of RAM in production.
* **Intelligent Multi-Stage Pipeline**:
  * **Gate 1 (Title Whitelist)**: User-defined comma-separated role titles.
  * **Gate 2 (Title Blacklist)**: Eliminates noise, sales, interns, or competing stacks.
  * **Gate 2.25 (Seniority Shield)**: Hard-drops executive leadership roles (Director, VP, Head of, Principal, Manager) before whitelist checks.
  * **Gate 2.5 (Company Blacklist)**: Drops low-quality agencies or aggregators.
  * **Gate 2.75 (Geofence)**: Validates target cities/regions, filters out foreign countries, and handles remote/work-from-home permissions.
  * **Gate 3 (Job Description Craft Validator)**: Asserts mandatory skills and blocks red-flag phrases (e.g. unpaid internships, commission-only).
* **Role Fingerprinting & Deduplication**:
  * Strips corporate suffixes (`Pvt Ltd`, `LLC`, `Technologies`) and seniority tokens (`Sr`, `Lead`, `Jr`) into an MD5 hash.
  * Identifies brand-new requisitions versus recycled agency postings.
  * Sequential Job ID Delta detection (~2.5M threshold) catches refreshed postings.
* **Chronological Oldest-First Dispatching**: Dispatches older verified jobs first so the newest postings always sit at the bottom of the Telegram channel feed.
* **One-Tap WhatsApp Share**: Generates clean forwarding snippets formatted to avoid Arabic/English bidirectional (BiDi) text inversion.

---

## Project Structure

```text
universal-linkedin-job-alert-bot/
├── .env.example                  # Comprehensive configuration template
├── Dockerfile                    # Hardened Alpine container definition
├── docker-compose.yml            # Container orchestration with volume mounts
├── universal_job_alert_bot.py    # Autonomous daemon script (100% generic)
├── test_universal_bot.py         # Unit test suite verifying all gate behaviors
└── README.md                     # Deployment and configuration guide
```

---

## Quick Start Deployment

### 1. Set Up Telegram Bot & Channel
1. Message `@BotFather` on Telegram and run `/newbot` to obtain your HTTP API Token.
2. Create a Telegram channel or group.
3. Add your bot as an Administrator with permission to post messages.
4. Retrieve your channel Chat ID (starts with `-100`).

### 2. Configure Environment Variables
Copy the template and edit [.env](file:///.env):

```bash
cp .env.example .env
nano .env
```

Set your Telegram credentials and search targets:
```bash
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrSTUvwxYZ"
TELEGRAM_CHAT_ID="-1001234567890"

SEARCH_QUERIES="DevOps Engineer, Cloud Architect, Platform Engineer"
SEARCH_LOCATIONS="United Arab Emirates | Bengaluru, Karnataka, India | Remote"
```

### 3. Verify Configuration Logic
Run the test suite to confirm all regex patterns and gate mechanics execute cleanly:
```bash
python3 -m unittest test_universal_bot.py
```

### 4. Run via Docker Compose
Build and launch the daemon in the background:
```bash
docker compose up -d --build
```

View live scraper output:
```bash
docker compose logs -f
```

---

## Example Configurations for Different Careers

### Scenario A: UI, Product & Web Designer
```bash
BOT_TITLE="UI & Product Design Job Alert Bot"
ALERT_ROLE_LABEL="DESIGN OPENING"
SEARCH_QUERIES="UI Designer, Product Designer, Web Designer, Figma Designer"
SEARCH_LOCATIONS="Dubai, UAE | Bengaluru, India | Remote"
GATE1_WHITELIST="ui designer, product designer, web designer, visual designer, interaction designer"
GATE2_BLACKLIST="motion designer, 3d artist, print designer, developer, intern"
SENIORITY_BLACKLIST="director, vp, head of, design manager"
REQUIRED_JD_KEYWORDS="figma, visual design, design system, component library"
BANNED_JD_KEYWORDS="reels, festival posters, unpaid internship"
TECH_TAGS="Figma:figma, Design Systems:design system|component library, Prototyping:wireframe|prototype"
WHATSAPP_GREETING="السلام عليكم ورحمة الله وبركاته,"
WHATSAPP_TEMPLATE="Saw this {role} opening at {company} ({location}). Check it out: {link}"
```

### Scenario B: Cloud, DevOps & SRE
```bash
BOT_TITLE="Cloud & DevOps Job Alert Bot"
ALERT_ROLE_LABEL="DEVOPS OPENING"
SEARCH_QUERIES="DevOps, SRE, Cloud Engineer, Platform Engineer"
SEARCH_LOCATIONS="Riyadh, Saudi Arabia | Dubai, UAE | India"
GATE1_WHITELIST="devops, sre, site reliability engineer, cloud engineer, platform engineer, kubernetes"
GATE2_BLACKLIST="frontend, backend, sales, intern, qa engineer"
SENIORITY_BLACKLIST="director, vp, head of, principal, manager"
REQUIRED_JD_KEYWORDS="kubernetes, terraform, aws, docker, ci/cd"
TECH_TAGS="Kubernetes:kubernetes|k8s, Terraform:terraform|opentofu, AWS:aws, ArgoCD:argocd|gitops"
WHATSAPP_GREETING="Assalamu Alaikum,"
WHATSAPP_TEMPLATE="Saw this {role} opening at {company} ({location}). Check it out: {link}"
```

### Scenario C: Backend Python / Go Engineer
```bash
BOT_TITLE="Backend Engineering Job Alert Bot"
ALERT_ROLE_LABEL="BACKEND OPENING"
SEARCH_QUERIES="Backend Engineer, Python Developer, Golang Engineer"
SEARCH_LOCATIONS="Germany | London, UK | Remote"
GATE1_WHITELIST="backend engineer, python engineer, golang engineer, software engineer backend"
GATE2_BLACKLIST="frontend, ui/ux, sales, recruiter, intern"
SENIORITY_BLACKLIST="director, vp, engineering manager"
REQUIRED_JD_KEYWORDS="python, golang, postgresql, redis, grpc, rest api, docker"
TECH_TAGS="Python:python|fastapi|django, Go:golang|\bgo\b, PostgreSQL:postgres|postgresql, Redis:redis"
WHATSAPP_SHARE_ENABLED="false"
```
