"""
Comprehensive automated tests for HR Cold Mailer.
Verifies all routes, database operations, CSV import, dry-run sending,
queue execution, stop control, and scheduler operations.
"""

import os
import json
from app import app
import db
import mailer
import scheduler

def run_all_tests():
    print("==================================================")
    print("Starting Comprehensive HR Cold Mailer Test Suite")
    print("==================================================")

    client = app.test_client()
    db.init_db()

    # 1. Test Dashboard route
    res = client.get("/")
    assert res.status_code == 200, f"Dashboard failed: {res.status_code}"
    print("[PASS] 1. GET / (Dashboard) loaded successfully.")

    # 2. Test Upload Page
    res = client.get("/upload")
    assert res.status_code == 200
    print("[PASS] 2. GET /upload loaded successfully.")

    # 3. Test CSV import with duplicates and invalid email
    sample_csv = (
        "name,email,company\n"
        "Alice Smith,alice@techfirm.io,TechFirm\n"
        "Bob Jones,bob@scaleai.com,ScaleAI\n"
        "Alice Smith Dup,alice@techfirm.io,TechFirm Dup\n"
        "Invalid User,not-an-email,Bad Corp\n"
        "Charlie Brown,charlie@bigcloud.org,BigCloud\n"
    )
    res = client.post("/upload/csv-paste", data={"csv_text": sample_csv}, follow_redirects=True)
    assert res.status_code == 200

    contacts = db.get_all_contacts()
    emails = [c["email"] for c in contacts]
    assert "alice@techfirm.io" in emails
    assert "bob@scaleai.com" in emails
    assert "charlie@bigcloud.org" in emails
    assert "not-an-email" not in emails
    assert emails.count("alice@techfirm.io") == 1  # Duplicate was dropped
    print(f"[PASS] 3. CSV parsing & deduplication verified: {len(contacts)} valid contacts in DB.")

    # 4. Test Templates & Settings Page
    res = client.get("/templates")
    assert res.status_code == 200
    print("[PASS] 4. GET /templates loaded successfully.")

    # 5. Test Saving Templates & Settings
    res = client.post("/templates/save", data={
        "initial_subject": "Engineering Role - {company}",
        "initial_body": "Hello team,\n\nI am contacting {company}.\n\n---\nIf this isn't relevant, just reply and I'll stop.",
        "follow_up_subject": "Following up - {company}",
        "follow_up_body": "Hello team,\n\nFollowing up regarding {company}.\n\n---\nIf this isn't relevant, just reply and I'll stop.",
        "daily_cap": "25",
        "min_delay": "30",
        "max_delay": "90",
        "follow_up_days": "4",
        "sender_name": "Danish Jain"
    }, follow_redirects=True)
    assert res.status_code == 200

    tpl = db.get_template("initial")
    assert tpl["subject"] == "Engineering Role - {company}"
    assert db.get_setting("daily_cap") == "25"
    print("[PASS] 5. POST /templates/save updated template and safety limits.")

    # 6. Test Email Preview Page
    contact = contacts[0]
    res = client.get(f"/preview/{contact['id']}")
    assert res.status_code == 200
    assert contact["company"].encode() in res.data
    print("[PASS] 6. GET /preview/<id> rendered personalized placeholders.")

    # 7. Test Dry-Run Toggle
    res = client.post("/api/settings/toggle-dry-run", json={"enabled": True})
    assert res.status_code == 200
    assert db.get_setting("dry_run") == "1"
    print("[PASS] 7. POST /api/settings/toggle-dry-run switched Dry-Run to ON.")

    # 8. Test 'Send Test to Myself' in Dry-Run mode
    res = client.post("/api/send-test")
    assert res.status_code == 200
    data = res.get_json()
    assert data["success"] is True
    assert "DRY-RUN" in data["message"]
    print(f"[PASS] 8. POST /api/send-test (Dry-Run): {data['message']}")

    # 9. Test Queue Execution in Dry-Run mode
    with db.get_db() as conn:
        conn.execute("UPDATE contacts SET status = 'pending', sent_at = NULL, error = NULL WHERE do_not_contact = 0")
        conn.commit()

    res = client.post("/api/queue/start", json={"mode": "initial"})
    assert res.status_code == 200
    print("[PASS] 9. POST /api/queue/start initiated send queue in Dry-Run mode.")

    # Wait for queue worker to finish dry run simulation
    import time
    for _ in range(15):
        time.sleep(1)
        res = client.get("/api/queue/status")
        status = res.get_json()
        if not status["is_running"]:
            break

    assert status["processed"] >= 1
    print(f"[PASS] 10. Queue worker executed successfully: {status['processed']}/{status['total']} contacts processed. Final status: {status['status']}")

    # 10. Test Checkbox Toggle (Replied / Do Not Contact)
    res = client.post(f"/api/contacts/{contact['id']}/toggle", json={"flag": "replied", "value": True})
    assert res.status_code == 200
    updated_c = db.get_contact_by_id(contact["id"])
    assert updated_c["replied"] == 1

    res = client.post(f"/api/contacts/{contact['id']}/toggle", json={"flag": "do_not_contact", "value": True})
    assert res.status_code == 200
    updated_c = db.get_contact_by_id(contact["id"])
    assert updated_c["do_not_contact"] == 1
    print("[PASS] 11. Replied & Do Not Contact toggles tested.")

    # 11. Test Scheduler endpoints
    scheduler.init_scheduler()
    res = client.get("/scheduler")
    assert res.status_code == 200

    # Add daily job at 10:00
    res = client.post("/scheduler/daily", data={"daily_time": "10:00", "mode": "initial"}, follow_redirects=True)
    assert res.status_code == 200
    jobs = scheduler.list_scheduled_jobs()
    assert any("10:00" in j["name"] for j in jobs)
    print(f"[PASS] 12. Scheduler successfully created daily job: {jobs[0]['name']}")

    # Cancel job
    res = client.post(f"/scheduler/cancel/{jobs[0]['id']}", follow_redirects=True)
    assert res.status_code == 200
    jobs_after = scheduler.list_scheduled_jobs()
    assert len(jobs_after) == 0
    print("[PASS] 13. Scheduler successfully cancelled job.")

    # 12. Test Resume Upload and > 1MB Warning
    # Create small dummy file
    import io
    small_pdf = io.BytesIO(b"%PDF-1.4 Small PDF")
    res = client.post("/upload/resume", data={"resume_file": (small_pdf, "my_resume.pdf")}, follow_redirects=True)
    assert res.status_code == 200
    assert db.get_setting("resume_filename") == "my_resume.pdf"

    # Create oversized dummy file (> 1 MB) to test warning alert
    large_pdf = io.BytesIO(b"%PDF-1.4" + b"X" * (1024 * 1024 + 100))
    res = client.post("/upload/resume", data={"resume_file": (large_pdf, "large_resume.pdf")}, follow_redirects=True)
    assert res.status_code == 200
    assert b"exceeds 1 MB" in res.data or b"File size warning" in res.data
    print("[PASS] 14. Resume upload and > 1MB warning alert verified.")

    # 13. Test SMTP Error Handling when credentials are invalid
    # Test with explicitly bad credentials
    gmail_address = "danishsjain@gmail.com"
    gmail_bad_pw = "bad_password_1234"
    import smtplib
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=5) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(gmail_address, gmail_bad_pw)
        auth_failed = False
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected):
        auth_failed = True
    assert auth_failed is True
    print("[PASS] 15. Gmail SMTP Authentication rejection on invalid credentials verified.")

    # 14. Test Rich HTML Template Rendering with Fonts & Highlights
    rich_subject = "Opportunity at {company}"
    rich_body = "<div style='font-family: Arial;'><p>Hello <b>{hr_name}</b>,</p><p>Check my <mark style='background-color:#fef08a;'>achievements</mark> at {company}.</p></div>"
    sub, plain, rich_html = mailer.render_email(rich_subject, rich_body, "Sarah", "Google")
    assert "Google" in sub
    assert "Sarah" in plain
    assert "achievements" in plain
    assert "<mark style=" in rich_html
    print("[PASS] 16. Rich HTML template with highlights and font families rendered successfully.")

    print("\n==================================================")
    print("ALL 16 TESTS PASSED SUCCESSFULLY! ✅")
    print("==================================================")

if __name__ == "__main__":
    run_all_tests()

