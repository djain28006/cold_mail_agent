"""
Mailer module for HR Cold Mailer.
Handles email formatting, PDF attachments, Gmail SMTP sending with retry/backoff,
dry-run simulations, and background send queue processing.
"""

import os
import re
import html
import random
import smtplib
import threading
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate
from dotenv import load_dotenv

import db

# Reload environment variables
load_dotenv(override=True)


def get_resume_path():
    """Returns absolute path to current resume PDF or None if not set/found."""
    filename = db.get_setting("resume_filename", "sample_resume.pdf")
    if not filename:
        return None
    base_dir = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(base_dir, "uploads", filename)
    if os.path.exists(path):
        return path
    return None


def get_smtp_credentials():
    """
    Reads GMAIL_ADDRESS and GMAIL_APP_PASSWORD from .env.
    NEVER logs or returns password in logs.
    """
    load_dotenv(override=True)
    address = os.getenv("GMAIL_ADDRESS", "danishsjain@gmail.com").strip()
    raw_password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    # Remove spaces if user copied with 4-letter groups
    clean_password = raw_password.replace(" ", "")
    return address, clean_password


def is_valid_email(email_str):
    """Simple robust regex check for email syntax."""
    if not email_str or not isinstance(email_str, str):
        return False
    pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return bool(re.match(pattern, email_str.strip()))


def render_template_text(template_text, hr_name, company):
    """
    Replaces {hr_name}, {first_name}, {last_name}, and {company} placeholders safely.
    Handles variations such as:
    - [First Name], {first_name}, {first name}, [firstname], [fname]
    - [Last Name], {last_name}, {lastname}, [lname]
    - [HR Name], {hr_name}, [Name], {name}, [Full Name], {full_name}
    - [Company Name], {company_name}, [Company], {company}, [Organization]
    Intelligently maps generic department contacts (e.g. 'Hiring Team', 'Careers')
    to 'Team' so cold emails remain natural (e.g. 'Hi Team,' rather than 'Hi Hiring,').
    """
    if not template_text:
        return ""

    # Parse and extract name parts cleanly
    full_name = (hr_name or "Team").strip()
    words = [w for w in full_name.split() if w]
    prefixes = {'mr.', 'mr', 'ms.', 'ms', 'mrs.', 'mrs', 'dr.', 'dr', 'prof.', 'prof'}
    if len(words) > 1 and words[0].lower() in prefixes:
        effective_words = words[1:]
    else:
        effective_words = words

    # Detect generic/team contact designations to produce natural greetings
    generic_dept = {'hiring', 'recruitment', 'recruiting', 'talent', 'careers', 'hr', 'admin', 'jobs', 'people', 'staffing'}
    if any(w.lower() in generic_dept for w in words):
        first_name = "Team"
        last_name = ""
    elif effective_words:
        first_name = effective_words[0].capitalize()
        last_name = " ".join(w.capitalize() for w in effective_words[1:]) if len(effective_words) > 1 else ""
    else:
        first_name = "Team"
        last_name = ""

    company_val = (company or "your team").strip()

    rendered = template_text

    # Match any {company}, [Company Name], [Company], {company_name}, <Company>, etc.
    company_pattern = r'(\{|\[|<)\s*(?:company(?:\s*name)?|organization|org|firm|business|startup|agency)\s*(\}|\]|>)'
    rendered = re.sub(company_pattern, company_val, rendered, flags=re.IGNORECASE)

    # Match any {first_name}, [First Name], [First_Name], [firstname], etc.
    first_name_pattern = r'(\{|\[|<)\s*(?:first(?:_|\s*)?name|fname|given(?:_|\s*)?name)\s*(\}|\]|>)'
    rendered = re.sub(first_name_pattern, first_name, rendered, flags=re.IGNORECASE)

    # Match any {last_name}, [Last Name], [Last_Name], [lastname], etc.
    last_name_pattern = r'(\{|\[|<)\s*(?:last(?:_|\s*)?name|lname|surname|family(?:_|\s*)?name)\s*(\}|\]|>)'
    rendered = re.sub(last_name_pattern, last_name, rendered, flags=re.IGNORECASE)

    # Match any {hr_name}, [HR Name], [Full Name], [name], {name}, <name>, etc.
    name_pattern = r'(\{|\[|<)\s*(?:full(?:_|\s*)?name|hr(?:_|\s*)?name|contact(?:_|\s*)?name|candidate(?:_|\s*)?name|name)\s*(\}|\]|>)'
    rendered = re.sub(name_pattern, full_name, rendered, flags=re.IGNORECASE)

    # Fallback replacements for exact tags
    rendered = rendered.replace("{hr_name}", full_name).replace("[HR Name]", full_name)
    rendered = rendered.replace("{first_name}", first_name).replace("[First Name]", first_name)
    rendered = rendered.replace("{last_name}", last_name).replace("[Last Name]", last_name)
    rendered = rendered.replace("{company}", company_val).replace("[Company Name]", company_val)

    return rendered


def html_to_plain_text(html_content):
    """
    Converts rich HTML email content into clean, readable plain text.
    Preserves paragraph breaks, list bullets, and converts separators.
    """
    if not html_content:
        return ""
    text = re.sub(r'<\s*br\s*/?>', '\n', html_content, flags=re.I)
    text = re.sub(r'</\s*(p|div|tr|h[1-6])\s*>', '\n\n', text, flags=re.I)
    text = re.sub(r'</\s*li\s*>', '\n', text, flags=re.I)
    text = re.sub(r'<\s*li[^>]*>', '• ', text, flags=re.I)
    text = re.sub(r'<\s*hr[^>]*>', '\n---\n', text, flags=re.I)
    # Strip remaining HTML tags
    text = re.sub(r'<[^>]+>', '', text)
    # Unescape HTML entities
    text = html.unescape(text)
    # Collapse multiple blank lines
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text


def is_html_content(content):
    """Detects whether content already contains rich HTML markup."""
    if not content:
        return False
    tags = r'<\s*(p|div|span|b|strong|i|em|u|mark|ul|ol|li|br|hr|font|h[1-6]|table|a)\b[^>]*>'
    return bool(re.search(tags, content, flags=re.I))


def wrap_html_email(body_html):
    """
    Wraps rich HTML body in a responsive, clean email container
    with cross-client typography and mobile viewport support.
    """
    if "<html" in body_html.lower():
        return body_html

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.6; color: #2d3748; background-color: #ffffff; margin: 0; padding: 20px;">
    <div style="max-width: 640px; margin: 0 auto;">
        {body_html}
    </div>
</body>
</html>"""


def render_email(subject_template, body_template, hr_name, company):
    """
    Unified email renderer. Replaces placeholders in subject and body.
    Supports both rich HTML templates and legacy plain-text templates,
    producing both a rendered plain text version and rendered HTML version.
    Returns: (rendered_subject, rendered_plain, rendered_html)
    """
    rendered_subject = render_template_text(subject_template, hr_name, company)
    raw_body = render_template_text(body_template, hr_name, company)

    if is_html_content(raw_body):
        rendered_plain = html_to_plain_text(raw_body)
        rendered_html = wrap_html_email(raw_body)
    else:
        rendered_plain = raw_body
        rendered_html = text_to_simple_html(raw_body)

    return rendered_subject, rendered_plain, rendered_html


def text_to_simple_html(text_content):
    """
    Converts plain text email into clean, simple, UTF-8 safe HTML
    with proper paragraph structure and styled opt-out footer.
    """
    escaped = html.escape(text_content)

    # Check for opt-out separator
    if "\n---\n" in escaped:
        main_part, footer_part = escaped.split("\n---\n", 1)
    elif "\n---" in escaped:
        main_part, footer_part = escaped.split("\n---", 1)
    else:
        main_part, footer_part = escaped, ""

    # Convert paragraphs
    paragraphs = [p.strip().replace("\n", "<br>") for p in main_part.split("\n\n") if p.strip()]
    body_html = "".join(f"<p style='margin: 0 0 16px 0;'>{p}</p>" for p in paragraphs)

    footer_html = ""
    if footer_part:
        footer_paragraphs = [p.strip().replace("\n", "<br>") for p in footer_part.split("\n\n") if p.strip()]
        footer_content = "".join(f"<p style='margin: 0 0 8px 0;'>{p}</p>" for p in footer_paragraphs)
        footer_html = f"""
        <hr style='border: none; border-top: 1px solid #e2e8f0; margin: 24px 0 16px 0;' />
        <div style='color: #718096; font-size: 13px; font-style: italic;'>
            {footer_content}
        </div>
        """

    full_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; font-size: 15px; line-height: 1.6; color: #2d3748; background-color: #ffffff; margin: 0; padding: 20px;">
    <div style="max-width: 640px; margin: 0 auto;">
        {body_html}
        {footer_html}
    </div>
</body>
</html>"""
    return full_html


def build_email_message(recipient_email, subject, plain_text, html_text, resume_path=None):
    """Constructs an EmailMessage instance with both text and HTML, plus PDF attachment."""
    address, _ = get_smtp_credentials()
    sender_name = db.get_setting("sender_name", "Danish Jain")
    from_header = f"{sender_name} <{address}>" if sender_name else address

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_header
    msg["To"] = recipient_email
    msg["Date"] = formatdate(localtime=True)

    # Plain text alternative
    msg.set_content(plain_text, charset="utf-8")

    # HTML alternative
    msg.add_alternative(html_text, subtype="html", charset="utf-8")

    # PDF Attachment
    if resume_path and os.path.exists(resume_path):
        with open(resume_path, "rb") as f:
            pdf_data = f.read()
        filename = os.path.basename(resume_path)
        msg.add_attachment(
            pdf_data,
            maintype="application",
            subtype="pdf",
            filename=filename
        )

    return msg


def send_email_smtp(recipient_email, subject, plain_text, html_text, resume_path=None, dry_run=False, max_retries=3):
    """
    Sends an email via Gmail SMTP (smtp.gmail.com:587 STARTTLS) with exponential backoff retry.
    If dry_run is True, validates and logs without contacting SMTP.
    Returns: (success: bool, message: str)
    """
    # 1. Validation
    if not is_valid_email(recipient_email):
        return False, f"Invalid email format: '{recipient_email}'"

    # 2. Check Dry-Run mode
    if dry_run:
        attachment_msg = f"Attached: {os.path.basename(resume_path)}" if (resume_path and os.path.exists(resume_path)) else "No attachment"
        return True, f"[DRY-RUN] Success! Would send to {recipient_email}. ({attachment_msg})"

    # 3. Check Credentials
    gmail_address, gmail_app_password = get_smtp_credentials()
    if not gmail_address:
        return False, "GMAIL_ADDRESS is not set in .env."
    if not gmail_app_password or gmail_app_password == "your_16_character_app_password_here":
        return False, (
            "Gmail App Password missing. Please set GMAIL_APP_PASSWORD in your .env file. "
            "To get one: Enable 2-Step Verification in Google Account -> Security -> App Passwords."
        )

    # 4. Build EmailMessage
    try:
        msg = build_email_message(recipient_email, subject, plain_text, html_text, resume_path)
    except Exception as e:
        return False, f"Error building email message: {str(e)}"

    # 5. Send with retry and backoff
    for attempt in range(1, max_retries + 1):
        try:
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(gmail_address, gmail_app_password)
                server.send_message(msg)
            return True, f"Email delivered successfully to {recipient_email}."
        except smtplib.SMTPAuthenticationError as auth_err:
            # Fatal authentication error - backoff will not fix wrong password
            return False, (
                "Gmail SMTP Authentication Failed. Google rejected your login credentials. "
                "Ensure 2-Step Verification is active on your Google account and you are using "
                "a 16-character App Password (not your personal Google account password) from "
                "https://myaccount.google.com/apppasswords."
            )
        except (smtplib.SMTPConnectError, smtplib.SMTPServerDisconnected, TimeoutError, OSError) as conn_err:
            if attempt < max_retries:
                backoff_wait = 2 ** attempt
                time.sleep(backoff_wait)
                continue
            else:
                return False, f"SMTP Connection error after {max_retries} attempts: {str(conn_err)}"
        except Exception as e:
            return False, f"Unexpected error while sending: {str(e)}"

    return False, f"Sending failed after {max_retries} attempts."


# ==============================================================================
# Background Send Queue Manager
# ==============================================================================

class SendQueueManager:
    def __init__(self):
        self.lock = threading.Lock()
        self.stop_requested = threading.Event()
        self.is_running = False
        self.worker_thread = None
        self.state = {
            "status": "idle",              # 'idle', 'running', 'paused', 'stopped', 'completed'
            "mode": "initial",             # 'initial' or 'follow_up'
            "is_dry_run": False,
            "total": 0,
            "processed": 0,
            "success_count": 0,
            "fail_count": 0,
            "current_email": "",
            "message": "Queue is idle.",
            "next_send_in_seconds": 0,
            "started_at": None
        }

    def get_status(self):
        with self.lock:
            # Update today's sent count
            sent_today = db.get_sent_today_count()
            daily_cap = int(db.get_setting("daily_cap", "30"))
            return {
                **self.state,
                "is_running": self.is_running,
                "sent_today": sent_today,
                "daily_cap": daily_cap
            }

    def start(self, mode="initial"):
        with self.lock:
            if self.is_running:
                return False, "Queue is already running."

            self.stop_requested.clear()
            self.is_running = True
            self.state["status"] = "running"
            self.state["mode"] = mode
            self.state["is_dry_run"] = db.get_setting("dry_run", "0") == "1"
            self.state["total"] = 0
            self.state["processed"] = 0
            self.state["success_count"] = 0
            self.state["fail_count"] = 0
            self.state["current_email"] = ""
            self.state["message"] = "Initializing send queue..."
            self.state["started_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            self.worker_thread = threading.Thread(target=self._run_queue, args=(mode,), daemon=True)
            self.worker_thread.start()
            return True, "Send queue started."

    def stop(self):
        with self.lock:
            if not self.is_running:
                return False, "Queue is not running."
            self.stop_requested.set()
            self.state["message"] = "Stopping queue..."
            return True, "Stop signal sent to queue."

    def _sleep_with_cancel_check(self, seconds):
        """Sleeps in short 0.5s ticks to allow instant cancellation on user stop."""
        start_time = time.time()
        while time.time() - start_time < seconds:
            if self.stop_requested.is_set():
                return False
            remaining = int(seconds - (time.time() - start_time))
            with self.lock:
                self.state["next_send_in_seconds"] = max(0, remaining)
                self.state["message"] = f"Delay: next email in {remaining}s (30-90s random delay)"
            time.sleep(0.5)
        with self.lock:
            self.state["next_send_in_seconds"] = 0
        return True

    def _run_queue(self, mode):
        try:
            dry_run = db.get_setting("dry_run", "0") == "1"
            daily_cap = int(db.get_setting("daily_cap", "30"))
            min_delay = int(db.get_setting("min_delay", "30"))
            max_delay = int(db.get_setting("max_delay", "90"))
            follow_up_days = int(db.get_setting("follow_up_days", "5"))
            resume_path = get_resume_path()

            # Retrieve template
            template_record = db.get_template(mode)
            if not template_record:
                with self.lock:
                    self.state["status"] = "error"
                    self.state["message"] = f"Error: '{mode}' email template not found in database."
                return

            subject_template = template_record["subject"]
            body_template = template_record["body"]

            # Retrieve recipient contacts
            if mode == "follow_up":
                contacts = db.get_follow_up_due_contacts(n_days=follow_up_days)
            else:
                contacts = db.get_pending_contacts()

            with self.lock:
                self.state["total"] = len(contacts)

            if not contacts:
                with self.lock:
                    self.state["status"] = "completed"
                    self.state["message"] = f"No pending contacts found for {mode} emails."
                db.log_email("system", mode, "info", f"Queue finished: No contacts pending for {mode}.")
                return

            db.log_email("system", mode, "info", f"Queue started for {len(contacts)} contacts. Mode: {mode}, Dry-run: {dry_run}")

            for index, contact in enumerate(contacts):
                # 1. Check stop signal
                if self.stop_requested.is_set():
                    with self.lock:
                        self.state["status"] = "stopped"
                        self.state["message"] = f"Queue stopped by user. Sent: {self.state['success_count']}, Failed: {self.state['fail_count']}."
                    db.log_email("system", mode, "warning", f"Queue stopped by user after {index} contacts.")
                    return

                # 2. Check Daily Cap
                sent_today = db.get_sent_today_count()
                if sent_today >= daily_cap:
                    with self.lock:
                        self.state["status"] = "paused"
                        self.state["message"] = f"Daily cap of {daily_cap} emails reached. Queue automatically paused."
                    db.log_email("system", mode, "warning", f"Daily cap reached ({daily_cap} mails). Queue paused.")
                    return

                # 3. Prepare email
                with self.lock:
                    self.state["current_email"] = contact["email"]
                    self.state["message"] = f"Sending ({index + 1}/{len(contacts)}) to {contact['email']}..."

                rendered_subject, rendered_plain, rendered_html = render_email(
                    subject_template, body_template, contact["name"], contact["company"]
                )

                # 4. Dispatch Email
                is_follow_up = (mode == "follow_up")
                success, log_msg = send_email_smtp(
                    recipient_email=contact["email"],
                    subject=rendered_subject,
                    plain_text=rendered_plain,
                    html_text=rendered_html,
                    resume_path=resume_path,
                    dry_run=dry_run
                )

                # 5. Record result in DB
                if success:
                    status_val = "dry_run" if dry_run else "sent"
                    db.update_contact_status(contact["id"], status_val, error=None, is_follow_up=is_follow_up)
                    db.log_email(contact["email"], mode, "dry_run" if dry_run else "success", log_msg, contact_id=contact["id"])
                    with self.lock:
                        self.state["success_count"] += 1
                else:
                    db.update_contact_status(contact["id"], "failed", error=log_msg, is_follow_up=is_follow_up)
                    db.log_email(contact["email"], mode, "failed", log_msg, contact_id=contact["id"])
                    with self.lock:
                        self.state["fail_count"] += 1

                with self.lock:
                    self.state["processed"] += 1

                # 6. Apply delay if more contacts remain
                if index + 1 < len(contacts):
                    if dry_run:
                        # In dry-run, use short 1-2 sec delay for rapid validation unless user stopped
                        delay = random.uniform(1.0, 2.5)
                    else:
                        # Standard 30 to 90 seconds random delay
                        delay = random.uniform(min_delay, max_delay)

                    can_continue = self._sleep_with_cancel_check(delay)
                    if not can_continue:
                        with self.lock:
                            self.state["status"] = "stopped"
                            self.state["message"] = "Queue stopped by user during delay."
                        return

            with self.lock:
                self.state["status"] = "completed"
                self.state["message"] = f"Queue completed! Sent: {self.state['success_count']}, Failed: {self.state['fail_count']}."
            db.log_email("system", mode, "info", f"Queue completed. Sent: {self.state['success_count']}, Failed: {self.state['fail_count']}.")

        except Exception as e:
            with self.lock:
                self.state["status"] = "error"
                self.state["message"] = f"Queue error: {str(e)}"
            db.log_email("system", mode, "failed", f"Queue exception: {str(e)}")
        finally:
            with self.lock:
                self.is_running = False


# Global queue manager singleton
queue_manager = SendQueueManager()

