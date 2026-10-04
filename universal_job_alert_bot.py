#!/usr/bin/env python3
"""
Universal LinkedIn Job Alert Bot
--------------------------------
A completely customizable, zero-hardcoding LinkedIn Job Alert Bot.
100% of operational knobs, role gates, geofencing, seniority traps,
technology taggers, and notification templates are configured via .env.

Architecture:
  1. LinkedIn Guest API with exponential backoff on HTTP 429 rate limits.
  2. Multi-Stage Pipeline configured via Environment Variables:
     - Gate 1: Whitelist (Target job titles)
     - Gate 2: Blacklist (Irrelevant titles)
     - Gate 2.25: Seniority Trap Shield (Director, VP, Manager, etc.)
     - Gate 2.5: Company Blacklist (Spam aggregators)
     - Gate 2.75: Location Geofence (Allowed vs Disallowed regions)
     - Gate 3: Job Description Validator (Required vs Banned skills/phrases)
  3. Dynamic Tech & Skill Tagging from user-defined regex pairs.
  4. Role Fingerprinting & Recency Delta Intelligence:
     - Dynamic MD5 fingerprinting (strips corporate suffixes & seniority noise).
     - Classifies roles into Fresh vs Reposted.
     - Sequential Job ID Delta detection (~2.5M threshold).
  5. Chronological Oldest-First Dispatching to Telegram.
  6. Optional WhatsApp One-Tap Share snippet with configurable greeting.
  7. SQLite WAL-Mode Persistence & Zero External Python Dependencies.
"""

import os
import sys
import re
import time
import json
import sqlite3
import hashlib
import random
import html
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

# Enable real-time unbuffered console output
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(line_buffering=True)


# ==========================================
# CONFIGURATION LOADER
# ==========================================
def load_env():
    env_path = os.environ.get("ENV_FILE", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip("'").strip('"')
                    os.environ[k] = v

load_env()

# Core Identity & Telegram
BOT_TITLE = os.environ.get("BOT_TITLE", "Universal LinkedIn Job Alert Bot")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_FRESH_CHAT_ID = os.environ.get("TELEGRAM_FRESH_CHAT_ID", TELEGRAM_CHAT_ID).strip()
TELEGRAM_REPOSTED_CHAT_ID = os.environ.get("TELEGRAM_REPOSTED_CHAT_ID", TELEGRAM_CHAT_ID).strip()

# Scheduling & Freshness
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "900"))
MAX_HOURS_OLD = int(os.environ.get("MAX_HOURS_OLD", "6"))
TIME_FILTER = os.environ.get("TIME_FILTER", "r10800")  # r10800 = past 3 hours, r86400 = past 24 hours
ID_DELTA_REPOST_THRESHOLD = int(os.environ.get("ID_DELTA_REPOST_THRESHOLD", "2500000"))

# Database path
DB_FILE = os.environ.get("DB_FILE", "data/seen_jobs.db")
if DB_FILE.startswith("/app/data") and not os.path.exists("/app"):
    DB_FILE = os.path.join("data", os.path.basename(DB_FILE))

# Search Queries (Comma-separated)
raw_queries = os.environ.get("SEARCH_QUERIES", "")
SEARCH_QUERIES = [q.strip() for q in raw_queries.split(",") if q.strip()]

# Search Locations (Pipe-separated)
raw_locs = os.environ.get("SEARCH_LOCATIONS", "")
SEARCH_LOCATIONS = [l.strip() for l in raw_locs.split("|") if l.strip()]

# Helper to build word-boundary regex from CSV
def build_regex_from_csv(csv_str):
    if not csv_str or not csv_str.strip():
        return None
    items = [re.escape(k.strip()) for k in csv_str.split(",") if k.strip()]
    if not items:
        return None
    return re.compile(r"(?i)\b(" + "|".join(items) + r")\b")

# Gate 1: Whitelist (Target job titles)
GATE1_WHITELIST_REGEX = build_regex_from_csv(os.environ.get("GATE1_WHITELIST", ""))

# Gate 2: Blacklist (Irrelevant titles)
GATE2_BLACKLIST_REGEX = build_regex_from_csv(os.environ.get("GATE2_BLACKLIST", ""))

# Gate 2.25: Seniority Blacklist (e.g. director, vp, head of, principal, staff, manager)
SENIORITY_BLACKLIST_REGEX = build_regex_from_csv(os.environ.get("SENIORITY_BLACKLIST", ""))

# Gate 2.5: Company Blacklist
COMPANY_BLACKLIST_REGEX = build_regex_from_csv(os.environ.get("COMPANY_BLACKLIST", ""))

# Gate 2.75: Geographic Geofence
# ALLOWED_LOCATIONS: If defined, jobs must match at least one token
ALLOWED_LOCATIONS_REGEX = build_regex_from_csv(os.environ.get("ALLOWED_LOCATIONS", ""))
# DISALLOWED_LOCATIONS: If defined, any location matching this is rejected
DISALLOWED_LOCATIONS_REGEX = build_regex_from_csv(os.environ.get("DISALLOWED_LOCATIONS", ""))
# REMOTE_ALLOWED_REGIONS: If a role is remote, allow it if it matches this regex
REMOTE_ALLOWED_REGIONS_REGEX = build_regex_from_csv(os.environ.get("REMOTE_ALLOWED_REGIONS", ""))

# Gate 3: Job Description Craft Validation
# REQUIRED_JD_KEYWORDS: If set, JD must contain at least MIN_REQUIRED_JD_MATCHES
REQUIRED_JD_REGEX = build_regex_from_csv(os.environ.get("REQUIRED_JD_KEYWORDS", ""))
MIN_REQUIRED_JD_MATCHES = int(os.environ.get("MIN_REQUIRED_JD_MATCHES", "1"))

# BANNED_JD_KEYWORDS: If set, JD must NOT exceed MAX_BANNED_JD_MATCHES
BANNED_JD_REGEX = build_regex_from_csv(os.environ.get("BANNED_JD_KEYWORDS", ""))
MAX_BANNED_JD_MATCHES = int(os.environ.get("MAX_BANNED_JD_MATCHES", "2"))

# Dynamic Tech & Skill Tagging
# Format: "Label:regex_pattern, Label2:regex_pattern2"
# Example: "Kubernetes:kubernetes|k8s, Terraform:terraform|opentofu, Figma:figma"
raw_tags = os.environ.get("TECH_TAGS", "")
TECH_TAG_DEFINITIONS = []
if raw_tags.strip():
    for item in raw_tags.split(","):
        if ":" in item:
            lbl, pat = item.split(":", 1)
            lbl = lbl.strip()
            pat = pat.strip()
            if lbl and pat:
                try:
                    compiled = re.compile(r"(?i)\b(" + pat + r")\b")
                    TECH_TAG_DEFINITIONS.append((lbl, compiled))
                except re.error as err:
                    print(f"⚠️ Warning: Invalid regex pattern for tag '{lbl}': {err}")

# Alert & Formatting Configuration
ALERT_ROLE_LABEL = os.environ.get("ALERT_ROLE_LABEL", "JOB OPENING").strip()
ALERT_HEADER_EMOJI = os.environ.get("ALERT_HEADER_EMOJI", "🟢").strip()

# WhatsApp Forwarding Configuration
WHATSAPP_SHARE_ENABLED = os.environ.get("WHATSAPP_SHARE_ENABLED", "true").lower() in ("true", "1", "yes")
WHATSAPP_GREETING = os.environ.get("WHATSAPP_GREETING", "").strip()
WHATSAPP_TEMPLATE = os.environ.get(
    "WHATSAPP_TEMPLATE",
    "Saw this {role} opening at {company} ({location}). Check it out: {link}"
)


# ==========================================
# DATABASE LAYER (SQLITE WAL MODE)
# ==========================================
def get_db_connection():
    db_dir = os.path.dirname(os.path.abspath(DB_FILE))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS seen_jobs (
            job_id TEXT PRIMARY KEY,
            fingerprint TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            work_mode TEXT,
            posted_text TEXT,
            link TEXT,
            category TEXT,
            reason TEXT,
            first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS role_fingerprints (
            fingerprint TEXT PRIMARY KEY,
            original_job_id TEXT,
            title TEXT,
            company TEXT,
            location TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            times_seen INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    return conn

def is_job_id_seen(job_id):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT category FROM seen_jobs WHERE job_id = ?", (str(job_id),))
    row = c.fetchone()
    conn.close()
    return row is not None

def get_max_known_job_id():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT job_id FROM seen_jobs")
    rows = c.fetchall()
    conn.close()
    max_id = 0
    for (jid,) in rows:
        if jid.isdigit():
            val = int(jid)
            if val > max_id:
                max_id = val
    return max_id

def mark_job_seen(job_id, fingerprint, title, company, location, work_mode, posted_text, link, category, reason):
    conn = get_db_connection()
    conn.execute("""
        INSERT INTO seen_jobs (job_id, fingerprint, title, company, location, work_mode, posted_text, link, category, reason, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(job_id) DO UPDATE SET 
            last_seen_at = CURRENT_TIMESTAMP,
            posted_text = excluded.posted_text
    """, (str(job_id), fingerprint, title, company, location, work_mode, posted_text, link, category, reason))
    conn.commit()
    conn.close()


# ==========================================
# ROLE FINGERPRINTING & REPOST CLASSIFICATION
# ==========================================
def compute_role_fingerprint(title, company, location, is_remote=False):
    """
    Computes a normalized MD5 hash representing the requisition.
    Strips common corporate suffixes and seniority words dynamically.
    """
    c = company.lower()
    c = re.sub(r'\b(pvt|private|ltd|limited|inc|incorporated|llc|technologies|technology|solutions|services|corp|corporation|group|agency|studio|consulting)\b', '', c)
    c = re.sub(r'[^a-z0-9]', '', c)
    if not c:
        c = re.sub(r'[^a-z0-9]', '', company.lower())

    t = title.lower()
    t = re.sub(r'\b(sr|senior|lead|principal|staff|junior|jr|level\s*\d+|l[1-4]|associate|trainee|ii|iii|iv)\b', '', t)
    t = re.sub(r'[^a-z0-9]', '', t)
    if not t:
        t = re.sub(r'[^a-z0-9]', '', title.lower())

    loc = location.lower()
    loc = re.sub(r'[^a-z0-9]', '', loc)
    if is_remote or "remote" in location.lower():
        loc = f"remote_{loc}"

    raw_key = f"{c}::{t}::{loc}"
    return hashlib.md5(raw_key.encode('utf-8')).hexdigest()

def check_and_update_fingerprint(fp, job):
    conn = get_db_connection()
    c = conn.cursor()
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    c.execute("SELECT original_job_id, times_seen, first_seen FROM role_fingerprints WHERE fingerprint = ?", (fp,))
    row = c.fetchone()

    if row is None:
        c.execute("""
            INSERT INTO role_fingerprints 
            (fingerprint, original_job_id, title, company, location, first_seen, last_seen, times_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (fp, str(job["id"]), job["title"], job["company"], job["location"], now_str, now_str))
        conn.commit()
        conn.close()
        return False, None
    else:
        orig_id, times_seen, first_seen = row
        if orig_id != str(job["id"]):
            new_times = times_seen + 1
            c.execute("""
                UPDATE role_fingerprints 
                SET last_seen = ?, times_seen = ? 
                WHERE fingerprint = ?
            """, (now_str, new_times, fp))
            conn.commit()
            conn.close()
            return True, {"original_job_id": orig_id, "times_seen": new_times, "first_seen": first_seen}
        else:
            conn.close()
            return False, None

def classify_job_freshness(job, fp, baseline_max_id):
    is_reposted, fp_info = check_and_update_fingerprint(fp, job)
    if is_reposted:
        times = fp_info["times_seen"]
        return "reposted", f"Recycled requisition (Seen {times}x since {fp_info['first_seen'][:10]})"

    jid_str = str(job["id"])
    if jid_str.isdigit() and baseline_max_id > 0:
        numeric_id = int(jid_str)
        delta = baseline_max_id - numeric_id
        if delta > ID_DELTA_REPOST_THRESHOLD:
            return "reposted", f"Refreshed requisition ID (ID delta: {delta // 1000000}M behind current baseline)"

    return "fresh", "Brand new requisition"


# ==========================================
# GATE FILTERING PIPELINE
# ==========================================
def passes_title_and_seniority(title):
    t = title.strip()

    # Gate 2.25: Seniority Blacklist (Runs first as an absolute block)
    if SENIORITY_BLACKLIST_REGEX:
        seniority_match = SENIORITY_BLACKLIST_REGEX.search(t)
        if seniority_match:
            return False, f"Seniority blacklist match: '{seniority_match.group(0)}'"

    # Gate 2: Title Blacklist
    if GATE2_BLACKLIST_REGEX:
        blacklist_match = GATE2_BLACKLIST_REGEX.search(t)
        if blacklist_match:
            return False, f"Title blacklist match: '{blacklist_match.group(0)}'"

    # Gate 1: Title Whitelist
    if GATE1_WHITELIST_REGEX:
        whitelist_match = GATE1_WHITELIST_REGEX.search(t)
        if not whitelist_match:
            return False, "Title does not match whitelist"
        matched_role = whitelist_match.group(0)
    else:
        matched_role = t

    return True, f"Matched: '{matched_role}'"

def matches_company_blacklist(company_name):
    if not company_name or not COMPANY_BLACKLIST_REGEX:
        return False, ""
    match = COMPANY_BLACKLIST_REGEX.search(company_name)
    if match:
        return True, f"Blacklisted company '{match.group(0)}'"
    return False, ""

def passes_location_filter(location_text, is_remote=False):
    if not location_text:
        return False, "Empty location field"

    loc = location_text.strip()

    # 1. Disallowed locations (Hard drop)
    if DISALLOWED_LOCATIONS_REGEX:
        bad_match = DISALLOWED_LOCATIONS_REGEX.search(loc)
        if bad_match:
            return False, f"Disallowed location detected: '{bad_match.group(0)}'"

    # 2. Remote check
    is_rem = is_remote or bool(re.search(r'(?i)\bremote\b', loc))
    if is_rem and REMOTE_ALLOWED_REGIONS_REGEX:
        rem_match = REMOTE_ALLOWED_REGIONS_REGEX.search(loc)
        if rem_match:
            return True, f"Remote role permitted in target region: '{rem_match.group(0)}'"

    # 3. Allowed locations
    if ALLOWED_LOCATIONS_REGEX:
        allowed_match = ALLOWED_LOCATIONS_REGEX.search(loc)
        if allowed_match:
            return True, f"Location matches allowed regions: '{allowed_match.group(0)}'"
        return False, f"Location '{loc}' outside allowed regions"

    # If no allowed locations filter is defined, accept by default
    return True, "Location accepted"

def evaluate_job_description(title, description, criteria=None):
    """
    Validates job criteria and description against configured skills and filters.
    """
    if criteria:
        emp_type = str(criteria.get("Employment type", "")).lower()
        seniority = str(criteria.get("Seniority level", "")).lower()

        # If user has a seniority blacklist, also check criteria metadata
        if SENIORITY_BLACKLIST_REGEX:
            for s_val in [emp_type, seniority]:
                m = SENIORITY_BLACKLIST_REGEX.search(s_val)
                if m:
                    return False, f"Seniority metadata match: '{m.group(0)}'"

    if not description:
        return True, []

    desc_lower = description.lower()

    # Banned JD keywords check
    if BANNED_JD_REGEX:
        banned_matches = BANNED_JD_REGEX.findall(desc_lower)
        if len(banned_matches) >= MAX_BANNED_JD_MATCHES:
            return False, f"Banned JD terms detected ({', '.join(set(banned_matches[:3]))})"

    # Required JD keywords check
    if REQUIRED_JD_REGEX:
        req_matches = REQUIRED_JD_REGEX.findall(desc_lower)
        if len(req_matches) < MIN_REQUIRED_JD_MATCHES:
            return False, f"Lacks mandatory skills (found {len(req_matches)} of {MIN_REQUIRED_JD_MATCHES} required)"

    # Extract dynamic tech tags
    badges = []
    for label, compiled_regex in TECH_TAG_DEFINITIONS:
        if compiled_regex.search(desc_lower) or compiled_regex.search(title.lower()):
            badges.append(label)

    return True, badges

def classify_work_mode(title, location, is_remote=False):
    combined = f"{title} {location}".lower()
    if is_remote or re.search(r'\b(remote|work from home|wfh|anywhere)\b', combined):
        return "remote", "🌐 Remote"
    if re.search(r'\b(hybrid|flexible|days in office)\b', combined):
        return "hybrid", "🔄 Hybrid"
    return "onsite", "🏢 On-site"


# ==========================================
# LINKEDIN GUEST API CLIENT
# ==========================================
def fetch_job_details(job_id):
    if not str(job_id).isdigit():
        return {}

    url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    }

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as response:
            html_content = response.read().decode('utf-8', errors='ignore')
            return parse_job_posting_html(html_content)
    except urllib.error.HTTPError as e:
        if e.code == 429:
            print(f"[-] HTTP 429 fetching JD for ID {job_id}. Skipping details.")
        return {}
    except Exception as e:
        return {}

def parse_job_posting_html(html_text):
    data = {"description": "", "apply_type": "Easy Apply", "criteria": {}}
    m_desc = re.search(r'<div class="show-more-less-html__markup[^>]*>(.*?)</div>', html_text, re.DOTALL)
    if m_desc:
        clean = re.sub(r'<[^>]+>', ' ', m_desc.group(1))
        data["description"] = html.unescape(clean).strip()

    if "apply-button" in html_text or "applyUrl" in html_text or "offsite" in html_text.lower():
        data["apply_type"] = "External Apply (Company Portal)"

    crit_matches = re.findall(
        r'<h3 class="description__job-criteria-subheader[^>]*>([^<]+)</h3>\s*<span class="description__job-criteria-text[^>]*>([^<]+)</span>',
        html_text
    )
    for sub, val in crit_matches:
        data["criteria"][sub.strip()] = val.strip()

    return data

def scrape_linkedin_guest_api(keywords, location, start=0):
    params = {
        "keywords": keywords,
        "location": location,
        "start": str(start),
    }
    if TIME_FILTER:
        params["f_TPR"] = TIME_FILTER

    # Check for explicit remote query
    is_remote_search = bool(re.search(r'(?i)\bremote\b', location) or re.search(r'(?i)\bremote\b', keywords))
    if is_remote_search:
        params["f_WT"] = "2"  # LinkedIn parameter for Remote

    url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?" + urllib.parse.urlencode(params)
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    req = urllib.request.Request(url, headers=headers)
    backoff = 5.0
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as response:
                content = response.read().decode('utf-8', errors='ignore')
                return parse_job_cards(content, is_remote_search)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print(f"[-] HTTP 429 Rate limited for '{keywords}' in '{location}'. Backing off {backoff}s...")
                time.sleep(backoff)
                backoff *= 2.0
            else:
                print(f"[-] HTTP {e.code} Error for '{keywords}' in '{location}'")
                return []
        except Exception as e:
            print(f"[-] Network error: {e}")
            return []
    return []

def parse_job_cards(html_content, is_remote=False):
    jobs = []
    card_chunks = re.split(r'<div[^>]*base-card[^>]*>', html_content)
    if len(card_chunks) <= 1:
        return jobs

    for chunk in card_chunks[1:]:
        m_id = re.search(r'jobPosting:([0-9]+)', chunk) or re.search(r'data-entity-urn="urn:li:jobPosting:([0-9]+)"', chunk)
        if not m_id:
            continue
        job_id = m_id.group(1)

        m_title = re.search(r'<h3 class="base-search-card__title[^>]*>([^<]+)</h3>', chunk)
        title = html.unescape(m_title.group(1)).strip() if m_title else "Job Opening"

        m_comp = re.search(r'<h4 class="base-search-card__subtitle[^>]*>.*?<a[^>]*>([^<]+)</a>', chunk, re.DOTALL)
        if not m_comp:
            m_comp = re.search(r'<h4 class="base-search-card__subtitle[^>]*>([^<]+)</h4>', chunk)
        company = html.unescape(m_comp.group(1)).strip() if m_comp else "Company Not Listed"

        m_loc = re.search(r'<span class="job-search-card__location[^>]*>([^<]+)</span>', chunk)
        location = html.unescape(m_loc.group(1)).strip() if m_loc else "Location Not Listed"

        m_time = re.search(r'<time[^>]*datetime="([^"]+)"[^>]*>([^<]+)</time>', chunk)
        if m_time:
            posted_text = m_time.group(2).strip()
            datetime_val = m_time.group(1).strip()
        else:
            posted_text = "Just Now"
            datetime_val = ""

        link = f"https://www.linkedin.com/jobs/view/{job_id}"
        jobs.append({
            "id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "posted_text": posted_text,
            "datetime_val": datetime_val,
            "link": link,
            "is_remote": is_remote
        })
    return jobs


# ==========================================
# AGE / FRESHNESS PARSER
# ==========================================
def parse_relative_age_hours(posted_text, datetime_val):
    if not posted_text:
        return 0.1
    t = posted_text.lower()
    if any(k in t for k in ["just now", "moments ago", "few seconds", "minute"]):
        return 0.1
    m_hr = re.search(r'(\d+)\s+hour', t)
    if m_hr:
        return float(m_hr.group(1))
    m_day = re.search(r'(\d+)\s+day', t)
    if m_day:
        return float(m_day.group(1)) * 24.0
    if datetime_val:
        try:
            d = datetime.fromisoformat(datetime_val)
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            diff = (now - d).total_seconds() / 3600.0
            return max(0.1, diff)
        except Exception:
            pass
    return 1.0


# ==========================================
# TELEGRAM DISPATCHER & MESSAGE FORMATTER
# ==========================================
def format_job_message(job, category, badges, jd_info):
    title = html.escape(job["title"])
    company = html.escape(job["company"])
    location = html.escape(job["location"])
    work_mode_code, work_mode_badge = classify_work_mode(job["title"], job["location"], job["is_remote"])
    apply_type = jd_info.get("apply_type", "Apply on LinkedIn")
    link = job["link"]

    criteria = jd_info.get("criteria", {})
    criteria_line = ""
    if criteria:
        parts = [f"{k}: {v}" for k, v in list(criteria.items())[:2]]
        if parts:
            criteria_line = f"\n💼 <b>Criteria:</b> {html.escape(' | '.join(parts))}"

    tags_line = ""
    if badges:
        tag_items = [f"🏷️ {html.escape(b)}" for b in badges]
        tags_line = f"\n🏷️ <b>Detected Skills:</b> {' | '.join(tag_items)}"

    if category == "reposted":
        header = f"🔄 <b>REPOSTED / RECYCLED {ALERT_ROLE_LABEL}</b>"
    else:
        header = f"{ALERT_HEADER_EMOJI} <b>FRESH {ALERT_ROLE_LABEL}!</b>"

    msg = (
        f"{header}\n\n"
        f"💼 <b>Role:</b> {title}\n"
        f"🏢 <b>Company:</b> {company}\n"
        f"📍 <b>Location:</b> {location} ({work_mode_badge})\n"
        f"⏱️ <b>Posted:</b> {job['posted_text']}\n"
        f"⚡ <b>Application:</b> {apply_type}"
        f"{criteria_line}"
        f"{tags_line}\n"
    )

    # Optional WhatsApp One-Tap Forwarding Snippet
    if WHATSAPP_SHARE_ENABLED:
        role_raw = job['title']
        comp_raw = job['company']
        loc_raw = job['location']
        loc_suffix = " - Remote" if work_mode_code == "remote" and "remote" not in loc_raw.lower() else ""

        body_text = WHATSAPP_TEMPLATE.format(
            role=role_raw,
            company=comp_raw,
            location=f"{loc_raw}{loc_suffix}",
            link=link
        )

        if WHATSAPP_GREETING:
            # Enforce line 1 for greeting and line 2 for body (prevents Arabic/English BiDi inversion)
            wa_snippet = f"{WHATSAPP_GREETING}\n{body_text}"
        else:
            wa_snippet = body_text

        msg += (
            f"\n📲 <b>One-Tap WhatsApp Share:</b>\n"
            f"<code>{html.escape(wa_snippet)}</code>\n"
        )

    msg += f"\n🔗 <a href=\"{link}\">Apply on LinkedIn</a>"
    return msg

def send_telegram_alert(message, category="fresh"):
    if not TELEGRAM_BOT_TOKEN:
        print("[!] TELEGRAM_BOT_TOKEN not configured. Skipping alert.")
        return False

    chat_id = TELEGRAM_FRESH_CHAT_ID if category == "fresh" else TELEGRAM_REPOSTED_CHAT_ID
    if not chat_id:
        chat_id = TELEGRAM_CHAT_ID
    if not chat_id:
        print("[!] No TELEGRAM_CHAT_ID configured. Skipping alert.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }

    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                retry_after = 5
                try:
                    res_json = json.loads(e.read().decode())
                    retry_after = res_json.get("parameters", {}).get("retry_after", 5)
                except Exception:
                    pass
                time.sleep(retry_after + 1)
            else:
                print(f"[!] Telegram HTTP {e.code} Error: {e.read().decode('utf-8', errors='ignore')}")
                return False
        except Exception as e:
            print(f"[!] Telegram Network Error: {e}")
            time.sleep(2)
    return False


# ==========================================
# MAIN RUN LOOP & SCAN CYCLE
# ==========================================
def run_scan_cycle():
    if not SEARCH_QUERIES or not SEARCH_LOCATIONS:
        print("[!] ERROR: SEARCH_QUERIES and SEARCH_LOCATIONS must be set in .env")
        return

    baseline_max_id = get_max_known_job_id()
    verified_jobs = []

    for loc in SEARCH_LOCATIONS:
        for query in SEARCH_QUERIES:
            is_remote_loc = bool("remote" in loc.lower() or "remote" in query.lower())
            loc_label = f"{loc} [REMOTE]" if is_remote_loc and "remote" not in loc.lower() else loc
            cards = scrape_linkedin_guest_api(query, loc, start=0)
            print(f"  🔎 [{loc_label}] '{query}': {len(cards)} cards fetched.")

            for job in cards:
                jid = job["id"]
                if is_job_id_seen(jid):
                    continue

                # Freshness Guardrail
                age_hours = parse_relative_age_hours(job["posted_text"], job["datetime_val"])
                if age_hours > MAX_HOURS_OLD:
                    mark_job_seen(jid, "", job["title"], job["company"], job["location"], "", job["posted_text"], job["link"], "rejected", f"Exceeded max age ({age_hours:.1f}h > {MAX_HOURS_OLD}h)")
                    continue

                # Gate 1 & 2: Title & Seniority Filter
                ok_title, reason_title = passes_title_and_seniority(job["title"])
                if not ok_title:
                    mark_job_seen(jid, "", job["title"], job["company"], job["location"], "", job["posted_text"], job["link"], "rejected", reason_title)
                    continue

                # Gate 2.5: Company Blacklist
                is_blacklisted_company, reason_comp = matches_company_blacklist(job["company"])
                if is_blacklisted_company:
                    mark_job_seen(jid, "", job["title"], job["company"], job["location"], "", job["posted_text"], job["link"], "rejected", reason_comp)
                    continue

                # Gate 2.75: Location Geofence
                ok_loc, reason_loc = passes_location_filter(job["location"], job["is_remote"])
                if not ok_loc:
                    mark_job_seen(jid, "", job["title"], job["company"], job["location"], "", job["posted_text"], job["link"], "rejected", reason_loc)
                    continue

                # Gate 3: Job Description Validation & Skill Tagging
                time.sleep(random.uniform(0.5, 1.2))
                jd_info = fetch_job_details(jid)
                ok_jd, badges_or_reason = evaluate_job_description(job["title"], jd_info.get("description", ""), jd_info.get("criteria", {}))
                if not ok_jd:
                    mark_job_seen(jid, "", job["title"], job["company"], job["location"], "", job["posted_text"], job["link"], "rejected", badges_or_reason)
                    continue

                badges = badges_or_reason if isinstance(badges_or_reason, list) else []

                # Role Fingerprint & Freshness Classification
                fp = compute_role_fingerprint(job["title"], job["company"], job["location"], job["is_remote"])
                cat, cat_reason = classify_job_freshness(job, fp, baseline_max_id)
                work_mode_code, _ = classify_work_mode(job["title"], job["location"], job["is_remote"])

                verified_jobs.append({
                    "job": job,
                    "category": cat,
                    "reason": cat_reason,
                    "fingerprint": fp,
                    "badges": badges,
                    "jd_info": jd_info,
                    "work_mode": work_mode_code,
                    "age_hours": age_hours
                })

            time.sleep(random.uniform(1.0, 2.0))

    if not verified_jobs:
        print("[*] Scan cycle complete. No new matching openings found.")
        return

    # Chronological Oldest-First Sorting
    # Older jobs dispatch first so the newest ("Just Now") roles sit at the bottom of the feed
    verified_jobs.sort(key=lambda x: x["age_hours"], reverse=True)
    print(f"[+] Dispatching {len(verified_jobs)} verified openings in chronological order...")

    for item in verified_jobs:
        j = item["job"]
        msg = format_job_message(j, item["category"], item["badges"], item["jd_info"])
        sent = send_telegram_alert(msg, category=item["category"])
        if sent:
            mark_job_seen(
                j["id"], item["fingerprint"], j["title"], j["company"],
                j["location"], item["work_mode"], j["posted_text"],
                j["link"], item["category"], item["reason"]
            )
            print(f"  ✅ Alert dispatched: '{j['title']}' @ {j['company']} ({item['category']})")
        else:
            print(f"  ❌ Failed to send alert: '{j['title']}' @ {j['company']}")
        time.sleep(2.0)


def main():
    print(f"==================================================")
    print(f"  {BOT_TITLE}")
    print(f"  Poll Interval: {POLL_INTERVAL_SECONDS}s | Lookback: {TIME_FILTER} | Max Age: {MAX_HOURS_OLD}h")
    print(f"  Target Queries: {', '.join(SEARCH_QUERIES)}")
    print(f"  Target Locations: {len(SEARCH_LOCATIONS)} regions")
    print(f"==================================================")

    while True:
        try:
            run_scan_cycle()
        except KeyboardInterrupt:
            print("\n[!] Bot shutting down cleanly.")
            break
        except Exception as e:
            print(f"[-] Unhandled exception in scan cycle: {e}")

        print(f"[*] Sleeping for {POLL_INTERVAL_SECONDS} seconds before next cycle...")
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
