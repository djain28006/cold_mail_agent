# HR Cold Mailer 🚀

A lightweight, automated local cold email outreach tool built for job seekers and candidates to reach HRs and hiring managers at scale while preserving high deliverability and avoiding spam filters.

Built with **Python 3.11 / 3.10**, **Flask**, **SQLite**, **APScheduler**, and vanilla HTML/CSS. **100% Free Tools Only.**

---

## 🌟 Key Features

1. **Gmail SMTP Delivery (`smtplib`)**:
   - Sends securely via `smtp.gmail.com:587` with STARTTLS.
   - Credentials read directly from `.env` via `python-dotenv`.
   - Google App Password is **never logged or displayed** in any UI or log file.
   - Built-in exponential backoff retry for transient network connection errors.
   - Informative troubleshooting for `SMTPAuthenticationError` (e.g. 2FA and Google App Password requirements).

2. **Resume PDF Attachment**:
   - Upload your resume PDF once; it is saved to `/uploads` and attached via `EmailMessage` to every outgoing email.
   - **Automatic size warning banner** if the uploaded PDF exceeds 1 MB (larger files often trigger spam filters).

3. **HR CSV Importer**:
   - Accepts CSV with columns: `name`, `email`, `company`.
   - Validates email formatting.
   - Automatically drops duplicate contacts within the CSV and prevents duplicate entries in the SQLite database.
   - Displays an interactive import preview table with counts of added, duplicates, and discarded rows.
   - Option to upload CSV file or paste raw CSV text directly.

4. **Rich Visual Template Editor & Highlights**:
   - Dual templates stored in SQLite: **Initial Outreach** and **N-Day Follow-Up**.
   - **Font Customization**: Choose between Modern Sans, Clean Arial, Classic Georgia Serif, Editorial Serif, Trebuchet MS, and Clean Monospace, with adjustable font sizes (13px–20px).
   - **Text Highlighting**: One-click highlighter tool with professional color swatches:
     - 🟡 Yellow (`#fef08a`) - standard highlight for key achievements
     - 🟢 Soft Green (`#bbf7d0`) - great for metrics and results
     - 🔵 Soft Blue (`#bfdbfe`) - great for tech stack and skills
     - 🟠 Soft Orange (`#fed7aa`) - great for call-to-actions
     - 🟣 Soft Purple (`#e9d5ff`)
   - **Text Styling & Palette**: Bold, Italic, Underline, Strikethrough, Bulleted/Numbered lists, and executive text colors.
   - **One-Click Placeholders**: Instant insert chips for `{hr_name}` and `{company}`.
   - **Opt-Out Footer Button**: One-click divider insert (`---` opt-out note).
   - **Visual / Raw HTML Toggle**: Seamlessly switch between the visual WYSIWYG editor and raw HTML source view.
   - **Multi-Part Email Delivery**: Automatically delivers responsive HTML styling to modern mail clients while generating a clean, un-bloated plain-text fallback for optimal deliverability.

5. **Dry-Run Mode Toggle**:
   - Easily toggle **Dry-Run: ON** from the navigation bar or settings.
   - In Dry-Run mode, all templates and PDF attachments are rendered and validated, and entries are logged to SQLite, but **no network SMTP packets are sent**.

6. **"Send Test to Myself" One-Click Button**:
   - Instant verification button in the header and preview page that sends a single test email with your resume attachment to `danishsjain@gmail.com`.

7. **Queue Safeguards & Stop Controller**:
   - **Random Delay**: Generates a random 30–90 second pause between consecutive emails to mimic human sending and avoid rate limiting.
   - **Daily Cap Safeguard**: Configurable daily sending limit (default: 30 emails/day). The queue automatically pauses when the limit is reached.
   - **Live Stop Button**: Stop the sending queue at any time.

8. **Automated Follow-ups**:
   - Tracks candidates who received the initial email.
   - Automatically identifies contacts who are due for follow-up after N days (default: 5 days).
   - Skips contacts marked as **Replied** or **Do Not Contact**.

9. **Scheduler (APScheduler)**:
   - Pick a specific date and time to auto-start a queue batch.
   - Schedule a recurring daily job (e.g. `10:00 AM`).
   - Real-time list of scheduled jobs with one-click cancellation.

10. **Interactive Dashboard**:
    - Counters for Sent, Failed, Pending, Follow-Up Due, Replied, and Daily Cap.
    - Clickable error tooltips for any failed delivery.
    - One-click "Retry Failed" with exponential backoff.
    - Live background queue progress bar without page reloads.

---

## 📁 Project Structure

```
c:\coldmail_agent\
├── app.py                     # Flask web server & route controllers
├── mailer.py                  # SMTP delivery, template rendering, queue manager
├── scheduler.py               # APScheduler background tasks (one-off & recurring)
├── db.py                      # SQLite database operations & schema migrations
├── coldmail.db                # SQLite database (auto-created on first run)
├── requirements.txt           # Python package dependencies
├── .env                       # Local secrets (ignored by Git)
├── .env.example               # Example configuration template
├── .gitignore                 # Git ignore rules
├── sample_hr_contacts.csv     # Sample CSV for testing
├── uploads/                   # Uploaded resume PDFs
│   └── sample_resume.pdf      # Sample resume PDF
├── static/
│   ├── style.css              # Clean, modern vanilla CSS styling
│   └── script.js              # Real-time queue polling & interactive UI logic
└── templates/
    ├── base.html              # Base layout with header and dry-run toggle
    ├── dashboard.html         # Main overview, contact list, queue controls, logs
    ├── upload.html            # Resume PDF & HR CSV upload page
    ├── templates_edit.html    # Template editor & sending limit settings
    ├── preview.html           # Live email & PDF attachment inspector
    └── scheduler.html         # APScheduler batch timing controls
```

---

## ⚡ Quickstart & Installation

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Gmail SMTP (`.env`)
1. Create or verify `.env` in the project root:
   ```env
   GMAIL_ADDRESS=danishsjain@gmail.com
   GMAIL_APP_PASSWORD=your_16_character_app_password
   ```
2. **How to get a Google App Password**:
   - Go to your [Google Account Security](https://myaccount.google.com/security).
   - Ensure **2-Step Verification** is turned ON.
   - Open [Google App Passwords](https://myaccount.google.com/apppasswords).
   - Enter an app name (e.g. `Cold Mailer`) and click **Create**.
   - Copy the 16-character code (e.g. `abcd efgh ijkl mnop`) and paste it into `.env` as `GMAIL_APP_PASSWORD`.
   - *Note: Do NOT use your normal Google account password.*

### 3. Run the Web Application
```bash
python app.py
```

### 4. Open the App in Your Browser
Visit:
```
http://localhost:5000
```

---

## 🧪 Testing Guide

### Step 1: Dry-Run Test
1. Toggle the **Dry Run** switch in the top-right navbar to **ON**.
2. Click **"Send Test to Myself"** or click **"Start Initial Outreach"**.
3. Notice how the logs and database record simulated deliveries (`[DRY-RUN] Success!`) without sending any actual emails.

### Step 2: Live Test Email
1. Add your Google App Password to `.env`.
2. Toggle **Dry Run** to **OFF**.
3. Click the **"Send Test to Myself"** button in the header.
4. Check your inbox at `danishsjain@gmail.com` for the test email with `sample_resume.pdf` attached.

