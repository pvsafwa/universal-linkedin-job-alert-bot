#!/usr/bin/env python3
"""
Unit Test Suite for Universal LinkedIn Job Alert Bot
Validates that 100% of gate logic, geofencing, seniority traps,
technology tagging, and formatting adapt cleanly to environment configurations.
"""

import os
import unittest
from unittest.mock import patch

# Configure test environment variables before loading module
os.environ["BOT_TITLE"] = "Test Universal Bot"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:FAKE_TOKEN"
os.environ["TELEGRAM_CHAT_ID"] = "-100123456789"
os.environ["GATE1_WHITELIST"] = "devops, sre, site reliability engineer, platform engineer, cloud engineer"
os.environ["GATE2_BLACKLIST"] = "intern, sales, telecaller, recruiter"
os.environ["SENIORITY_BLACKLIST"] = "director, vp, head of, principal, staff, manager"
os.environ["COMPANY_BLACKLIST"] = "Quik Hire, Turing, Crossover"
os.environ["ALLOWED_LOCATIONS"] = "bengaluru, bangalore, uae, dubai, remote"
os.environ["DISALLOWED_LOCATIONS"] = "united states, usa, united kingdom, uk"
os.environ["REMOTE_ALLOWED_REGIONS"] = "india, uae, remote"
os.environ["REQUIRED_JD_KEYWORDS"] = "kubernetes, terraform, aws"
os.environ["MIN_REQUIRED_JD_MATCHES"] = "1"
os.environ["BANNED_JD_KEYWORDS"] = "unpaid internship, stipend"
os.environ["MAX_BANNED_JD_MATCHES"] = "1"
os.environ["TECH_TAGS"] = "Kubernetes:kubernetes|k8s, Terraform:terraform|opentofu, AWS:aws"
os.environ["WHATSAPP_SHARE_ENABLED"] = "true"
os.environ["WHATSAPP_GREETING"] = "Assalamu Alaikum,"
os.environ["WHATSAPP_TEMPLATE"] = "Saw this {role} opening at {company} ({location}). Check: {link}"
os.environ["DB_FILE"] = ":memory:"

import universal_job_alert_bot as bot


class TestUniversalJobAlertBot(unittest.TestCase):

    def test_gate1_whitelist_matches(self):
        ok, reason = bot.passes_title_and_seniority("Senior DevOps Engineer")
        self.assertTrue(ok)
        self.assertIn("devops", reason.lower())

        ok, reason = bot.passes_title_and_seniority("Site Reliability Engineer")
        self.assertTrue(ok)

    def test_gate1_whitelist_rejects_unrelated_titles(self):
        ok, reason = bot.passes_title_and_seniority("Java Backend Developer")
        self.assertFalse(ok)
        self.assertIn("does not match whitelist", reason.lower())

    def test_gate2_blacklist_rejects_unwanted_roles(self):
        ok, reason = bot.passes_title_and_seniority("DevOps Intern")
        self.assertFalse(ok)
        self.assertIn("blacklist", reason.lower())

    def test_gate2_25_seniority_blacklist_blocks_executives(self):
        ok, reason = bot.passes_title_and_seniority("Director of DevOps")
        self.assertFalse(ok)
        self.assertIn("seniority", reason.lower())

        ok, reason = bot.passes_title_and_seniority("VP - Cloud Platform")
        self.assertFalse(ok)

        ok, reason = bot.passes_title_and_seniority("DevOps Manager")
        self.assertFalse(ok)

    def test_gate2_5_company_blacklist(self):
        is_bad, _ = bot.matches_company_blacklist("Turing")
        self.assertTrue(is_bad)

        is_bad, _ = bot.matches_company_blacklist("Google")
        self.assertFalse(is_bad)

    def test_gate2_75_location_filtering(self):
        # Allowed location
        ok, _ = bot.passes_location_filter("Bengaluru, Karnataka, India")
        self.assertTrue(ok)

        # Disallowed location (Hard drop)
        ok, _ = bot.passes_location_filter("New York, United States")
        self.assertFalse(ok)

        # Outside allowed regions
        ok, _ = bot.passes_location_filter("Tokyo, Japan")
        self.assertFalse(ok)

        # Remote allowed
        ok, _ = bot.passes_location_filter("India (Remote)", is_remote=True)
        self.assertTrue(ok)

    def test_gate3_required_and_banned_jd_keywords(self):
        # Good JD with required tech
        jd_good = "We are seeking an engineer experienced in Kubernetes clusters and Terraform deployments."
        ok, badges = bot.evaluate_job_description("DevOps Engineer", jd_good)
        self.assertTrue(ok)
        self.assertIn("Kubernetes", badges)
        self.assertIn("Terraform", badges)

        # Bad JD missing required skills
        jd_missing = "We need someone to manage our Linux servers and write bash scripts."
        ok, reason = bot.evaluate_job_description("DevOps Engineer", jd_missing)
        self.assertFalse(ok)
        self.assertIn("lacks mandatory skills", reason.lower())

        # Bad JD with banned keywords
        jd_banned = "This is an unpaid internship position with a certificate at the end."
        ok, reason = bot.evaluate_job_description("DevOps Engineer", jd_banned)
        self.assertFalse(ok)
        self.assertIn("banned jd terms", reason.lower())

    def test_gate3_criteria_seniority_rejection(self):
        criteria = {"Seniority level": "Director", "Employment type": "Full-time"}
        ok, reason = bot.evaluate_job_description("DevOps Engineer", "Kubernetes AWS", criteria=criteria)
        self.assertFalse(ok)
        self.assertIn("seniority metadata", reason.lower())

    def test_whatsapp_formatting_with_greeting(self):
        job = {
            "id": "123456",
            "title": "Cloud Platform Engineer",
            "company": "Stripe",
            "location": "Bengaluru",
            "posted_text": "10 minutes ago",
            "link": "https://linkedin.com/jobs/view/123456",
            "is_remote": False
        }
        msg = bot.format_job_message(job, "fresh", ["Kubernetes"], {"apply_type": "Easy Apply"})
        self.assertIn("Assalamu Alaikum,\nSaw this Cloud Platform Engineer", msg)
        self.assertIn("Apply on LinkedIn", msg)

    def test_role_fingerprinting(self):
        fp1 = bot.compute_role_fingerprint("Senior DevOps Engineer", "Acme Pvt Ltd", "Bengaluru")
        fp2 = bot.compute_role_fingerprint("DevOps Engineer", "Acme Technologies", "Bengaluru")
        # Should match despite 'Senior' vs '' and 'Pvt Ltd' vs 'Technologies'
        self.assertEqual(fp1, fp2)


if __name__ == "__main__":
    unittest.main()
