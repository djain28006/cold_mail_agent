"""
Database layer for HR Cold Mailer.
Uses SQLite with WAL mode for safe multi-threaded access.
"""

import os
import sqlite3
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "coldmail.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def init_db():
    with get_db() as conn:
        cursor = conn.cursor()

        # Contacts table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                company TEXT NOT NULL,
                status TEXT DEFAULT 'pending',          -- 'pending', 'sent', 'failed', 'dry_run'
                sent_at TIMESTAMP NULL,
                error TEXT NULL,
                follow_up_status TEXT DEFAULT 'pending', -- 'pending', 'sent', 'failed', 'skipped'
                follow_up_sent_at TIMESTAMP NULL,
                follow_up_error TEXT NULL,
                replied INTEGER DEFAULT 0,              -- 0 or 1
                do_not_contact INTEGER DEFAULT 0,       -- 0 or 1
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Email templates table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL UNIQUE,              -- 'initial', 'follow_up'
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Settings table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Email logs table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS email_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id INTEGER NULL,
                recipient_email TEXT NOT NULL,
                email_type TEXT NOT NULL,               -- 'test', 'initial', 'follow_up', 'reply', 'open'
                status TEXT NOT NULL,                   -- 'success', 'failed', 'dry_run', 'warning', 'info'
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Scheduled jobs metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_jobs (
                id TEXT PRIMARY KEY,
                job_name TEXT NOT NULL,
                schedule_type TEXT NOT NULL,            -- 'date', 'daily'
                schedule_value TEXT NOT NULL,
                status TEXT DEFAULT 'active',           -- 'active', 'completed', 'cancelled'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Schema migrations for open tracking & reply detection
        cursor.execute("PRAGMA table_info(contacts)")
        cols = [r["name"] for r in cursor.fetchall()]
        if "opened_at" not in cols:
            cursor.execute("ALTER TABLE contacts ADD COLUMN opened_at TIMESTAMP NULL")
        if "open_count" not in cols:
            cursor.execute("ALTER TABLE contacts ADD COLUMN open_count INTEGER DEFAULT 0")
        if "reply_snippet" not in cols:
            cursor.execute("ALTER TABLE contacts ADD COLUMN reply_snippet TEXT NULL")
        if "replied_at" not in cols:
            cursor.execute("ALTER TABLE contacts ADD COLUMN replied_at TIMESTAMP NULL")

        # Default settings if not already present
        default_settings = {
            "daily_cap": "30",
            "min_delay": "30",
            "max_delay": "90",
            "follow_up_days": "5",
            "dry_run": "0",
            "resume_filename": "sample_resume.pdf",
            "sender_email": "danishsjain@gmail.com",
            "sender_name": "Danish Jain"
        }
        for key, val in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(val)))

        # Default initial email template
        cursor.execute("SELECT id FROM templates WHERE type = 'initial'")
        if not cursor.fetchone():
            initial_subject = "Software Engineer Application - {company}"
            initial_body = (
                "Hello team,\n\n"
                "I hope this email finds you well.\n\n"
                "I am writing to express my strong interest in software engineering opportunities at {company}. "
                "With hands-on experience building scalable backend architectures, full-stack web applications, "
                "and workflow automations, I would love to explore how my skills can contribute to your engineering goals.\n\n"
                "I have attached my resume for your review. Please let me know if you would be open to a brief chat "
                "or if there is an open role that aligns with my profile.\n\n"
                "Thank you for your time and consideration.\n\n"
                "Best regards,\n"
                "Danish Jain\n"
                "danishsjain@gmail.com\n\n"
                "---\n"
                "If this isn't relevant, just reply and I'll stop."
            )
            cursor.execute(
                "INSERT INTO templates (type, subject, body) VALUES ('initial', ?, ?)",
                (initial_subject, initial_body)
            )

        # Default follow-up email template
        cursor.execute("SELECT id FROM templates WHERE type = 'follow_up'")
        if not cursor.fetchone():
            follow_up_subject = "Following up: Software Engineer Application - {company}"
            follow_up_body = (
                "Hello team,\n\n"
                "I wanted to quickly follow up on my previous note regarding software engineering opportunities at {company}.\n\n"
                "I understand you are likely busy, but I remain very enthusiastic about {company} and would welcome a brief conversation "
                "if there is an opportunity to connect.\n\n"
                "My resume remains attached for your convenience.\n\n"
                "Thank you again for your time!\n\n"
                "Best regards,\n"
                "Danish Jain\n"
                "danishsjain@gmail.com\n\n"
                "---\n"
                "If this isn't relevant, just reply and I'll stop."
            )
            cursor.execute(
                "INSERT INTO templates (type, subject, body) VALUES ('follow_up', ?, ?)",
                (follow_up_subject, follow_up_body)
            )

        conn.commit()


# --- Contact Operations ---

def get_contact_by_id(contact_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,))
        return cursor.fetchone()


def get_all_contacts(filter_status=None, search=None):
    with get_db() as conn:
        cursor = conn.cursor()
        query = "SELECT * FROM contacts WHERE 1=1"
        params = []

        if filter_status:
            if filter_status == "pending":
                query += " AND status = 'pending' AND do_not_contact = 0"
            elif filter_status == "sent":
                query += " AND status = 'sent'"
            elif filter_status == "failed":
                query += " AND status = 'failed'"
            elif filter_status == "dry_run":
                query += " AND status = 'dry_run'"
            elif filter_status == "replied":
                query += " AND replied = 1"
            elif filter_status == "do_not_contact":
                query += " AND do_not_contact = 1"
            elif filter_status == "follow_up_due":
                follow_up_days = int(get_setting("follow_up_days", "5"))
                cutoff = (datetime.now() - timedelta(days=follow_up_days)).strftime("%Y-%m-%d %H:%M:%S")
                query += """ AND status = 'sent' 
                             AND (follow_up_status IS NULL OR follow_up_status = 'pending') 
                             AND sent_at <= ? 
                             AND replied = 0 
                             AND do_not_contact = 0"""
                params.append(cutoff)

        if search:
            query += " AND (name LIKE ? OR email LIKE ? OR company LIKE ?)"
            term = f"%{search}%"
            params.extend([term, term, term])

        query += " ORDER BY id DESC"
        cursor.execute(query, params)
        return cursor.fetchall()


def import_contacts(contacts_list):
    """
    Imports a list of dicts: [{'name': ..., 'email': ..., 'company': ...}].
    Drops duplicate emails inside the batch and ignores or updates existing DB contacts.
    Returns: (added_count, duplicate_count, error_count)
    """
    added_count = 0
    duplicate_count = 0
    error_count = 0

    seen_emails_in_batch = set()

    with get_db() as conn:
        cursor = conn.cursor()
        for c in contacts_list:
            name = (c.get("name") or "").strip()
            email = (c.get("email") or "").strip().lower()
            company = (c.get("company") or "").strip()

            if not email or "@" not in email:
                error_count += 1
                continue

            if email in seen_emails_in_batch:
                duplicate_count += 1
                continue
            seen_emails_in_batch.add(email)

            try:
                # Try inserting
                cursor.execute("""
                    INSERT INTO contacts (name, email, company, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """, (name, email, company))
                added_count += 1
            except sqlite3.IntegrityError:
                # Email already exists in DB
                duplicate_count += 1

        conn.commit()

    return added_count, duplicate_count, error_count


def update_contact_status(contact_id, status, error=None, is_follow_up=False):
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if is_follow_up:
            cursor.execute("""
                UPDATE contacts 
                SET follow_up_status = ?, follow_up_sent_at = ?, follow_up_error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, now if status in ('sent', 'dry_run') else None, error, contact_id))
        else:
            cursor.execute("""
                UPDATE contacts 
                SET status = ?, sent_at = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, now if status in ('sent', 'dry_run') else None, error, contact_id))
        conn.commit()


def toggle_contact_flag(contact_id, flag_name, value):
    if flag_name not in ("replied", "do_not_contact"):
        return False
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE contacts SET {flag_name} = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (1 if value else 0, contact_id))
        conn.commit()
    return True


def delete_contact(contact_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
        conn.commit()


def clear_all_contacts():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM contacts")
        conn.commit()


def bulk_update_contacts(contact_ids, action):
    """Performs bulk actions on selected contacts: 'replied', 'do_not_contact', 'reset_pending', 'delete'."""
    if not contact_ids:
        return 0
    with get_db() as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in contact_ids)
        if action == "replied":
            cursor.execute(f"UPDATE contacts SET replied = 1, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", contact_ids)
        elif action == "do_not_contact":
            cursor.execute(f"UPDATE contacts SET do_not_contact = 1, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", contact_ids)
        elif action == "reset_pending":
            cursor.execute(f"UPDATE contacts SET status = 'pending', sent_at = NULL, error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id IN ({placeholders})", contact_ids)
        elif action == "delete":
            cursor.execute(f"DELETE FROM contacts WHERE id IN ({placeholders})", contact_ids)
        count = cursor.rowcount
        conn.commit()
        return count


def record_email_open(contact_id):
    """Increments open count and records the timestamp when an HR opens the email."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE contacts 
            SET open_count = COALESCE(open_count, 0) + 1, 
                opened_at = ?, 
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (now, contact_id))
        conn.commit()


def record_contact_reply(contact_id, snippet=None):
    """Marks a contact as replied and records reply snippet and timestamp."""
    with get_db() as conn:
        cursor = conn.cursor()
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("""
            UPDATE contacts 
            SET replied = 1, 
                replied_at = ?, 
                reply_snippet = ?, 
                updated_at = CURRENT_TIMESTAMP 
            WHERE id = ?
        """, (now, snippet, contact_id))
        conn.commit()


def get_sent_contact_emails():
    """Returns list of contacts that have been sent an email and haven't replied yet."""
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, email, company, sent_at 
            FROM contacts 
            WHERE status = 'sent' AND replied = 0
        """)
        return cursor.fetchall()


def reset_failed_contacts():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE contacts 
            SET status = 'pending', error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE status = 'failed'
        """)
        count = cursor.rowcount
        conn.commit()
    return count


def get_pending_contacts(limit=None):
    with get_db() as conn:
        cursor = conn.cursor()
        query = """
            SELECT * FROM contacts 
            WHERE status = 'pending' 
              AND do_not_contact = 0 
            ORDER BY id ASC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        cursor.execute(query)
        return cursor.fetchall()


def get_follow_up_due_contacts(n_days=5, limit=None):
    cutoff = (datetime.now() - timedelta(days=n_days)).strftime("%Y-%m-%d %H:%M:%S")
    with get_db() as conn:
        cursor = conn.cursor()
        query = """
            SELECT * FROM contacts 
            WHERE status = 'sent' 
              AND (follow_up_status IS NULL OR follow_up_status = 'pending') 
              AND sent_at <= ? 
              AND replied = 0 
              AND do_not_contact = 0
            ORDER BY sent_at ASC
        """
        if limit:
            query += f" LIMIT {int(limit)}"
        cursor.execute(query, (cutoff,))
        return cursor.fetchall()


def get_sent_today_count():
    today_start = datetime.now().strftime("%Y-%m-%d 00:00:00")
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT COUNT(*) FROM contacts 
            WHERE status IN ('sent', 'dry_run') AND sent_at >= ?
        """, (today_start,))
        row = cursor.fetchone()
        return row[0] if row else 0


def get_stats():
    follow_up_days = int(get_setting("follow_up_days", "5"))
    cutoff = (datetime.now() - timedelta(days=follow_up_days)).strftime("%Y-%m-%d %H:%M:%S")

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM contacts")
        total = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE status = 'sent'")
        sent = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE status = 'pending' AND do_not_contact = 0")
        pending = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE status = 'failed'")
        failed = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE status = 'dry_run'")
        dry_run = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE replied = 1")
        replied = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE open_count > 0")
        opened = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE do_not_contact = 1")
        dnc = cursor.fetchone()[0]

        cursor.execute("""
            SELECT COUNT(*) FROM contacts 
            WHERE status = 'sent' 
              AND (follow_up_status IS NULL OR follow_up_status = 'pending') 
              AND sent_at <= ? 
              AND replied = 0 
              AND do_not_contact = 0
        """, (cutoff,))
        follow_up_due = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM contacts WHERE follow_up_status = 'sent'")
        follow_up_sent = cursor.fetchone()[0]

    sent_today = get_sent_today_count()
    daily_cap = int(get_setting("daily_cap", "30"))

    return {
        "total": total,
        "sent": sent,
        "pending": pending,
        "failed": failed,
        "dry_run": dry_run,
        "replied": replied,
        "opened": opened,
        "do_not_contact": dnc,
        "follow_up_due": follow_up_due,
        "follow_up_sent": follow_up_sent,
        "sent_today": sent_today,
        "daily_cap": daily_cap
    }


# --- Template Operations ---

def get_template(template_type="initial"):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM templates WHERE type = ?", (template_type,))
        return cursor.fetchone()


def save_template(template_type, subject, body):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO templates (type, subject, body, updated_at) 
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(type) DO UPDATE SET 
                subject = excluded.subject,
                body = excluded.body,
                updated_at = CURRENT_TIMESTAMP
        """, (template_type, subject.strip(), body.strip()))
        conn.commit()


# --- Settings Operations ---

def get_setting(key, default=None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, str(value)))
        conn.commit()


def get_all_settings():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        return {r["key"]: r["value"] for r in rows}


# --- Email Logs Operations ---

def log_email(recipient_email, email_type, status, message, contact_id=None):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO email_logs (contact_id, recipient_email, email_type, status, message, timestamp)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (contact_id, recipient_email, email_type, status, message))
        conn.commit()


def get_recent_logs(limit=50):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM email_logs ORDER BY id DESC LIMIT ?
        """, (int(limit),))
        return cursor.fetchall()


def clear_logs():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM email_logs")
        conn.commit()


# --- Scheduled Jobs Operations ---

def record_scheduled_job(job_id, job_name, schedule_type, schedule_value):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO scheduled_jobs (id, job_name, schedule_type, schedule_value, status, created_at)
            VALUES (?, ?, ?, ?, 'active', CURRENT_TIMESTAMP)
        """, (job_id, job_name, schedule_type, schedule_value))
        conn.commit()


def cancel_scheduled_job_record(job_id):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE scheduled_jobs SET status = 'cancelled' WHERE id = ?", (job_id,))
        conn.commit()


def get_scheduled_job_records():
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM scheduled_jobs WHERE status = 'active' ORDER BY created_at DESC")
        return cursor.fetchall()

