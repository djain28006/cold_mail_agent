"""
HR Cold Mailer - Flask Web Application
Entry point for web UI, background queue controls, and REST endpoints.
"""

import os
import csv
import io
from datetime import datetime
from flask import (
    Flask, render_template, request, redirect, url_for,
    flash, jsonify, send_from_directory, session
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

import db
import mailer
import scheduler

# Load environment
load_dotenv(override=True)

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "coldmailer-default-secret-key-3.11")

# Base upload folder
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max request limit


@app.context_processor
def inject_global_data():
    """Injects common context attributes to all Jinja templates."""
    is_dry_run = db.get_setting("dry_run", "0") == "1"
    sender_email = db.get_setting("sender_email", os.getenv("GMAIL_ADDRESS", "danishsjain@gmail.com"))
    return {
        "is_dry_run": is_dry_run,
        "sender_address": sender_email,
        "now_year": datetime.now().year
    }


# ==============================================================================
# Web UI Page Routes
# ==============================================================================

@app.route("/")
def dashboard():
    """Main Dashboard with counts, send queue controls, contacts, and logs."""
    status_filter = request.args.get("status")
    stats = db.get_stats()
    contacts = db.get_all_contacts(filter_status=status_filter)
    logs = db.get_recent_logs(limit=40)
    return render_template(
        "dashboard.html",
        stats=stats,
        contacts=contacts,
        logs=logs,
        current_filter=status_filter
    )


@app.route("/upload")
def upload_page():
    """Resume PDF and HR CSV Upload Page."""
    resume_filename = db.get_setting("resume_filename", "sample_resume.pdf")
    resume_path = os.path.join(app.config["UPLOAD_FOLDER"], resume_filename) if resume_filename else None

    resume_info = {
        "exists": False,
        "filename": resume_filename,
        "size_kb": 0,
        "size_mb": 0
    }
    if resume_path and os.path.exists(resume_path):
        size_bytes = os.path.getsize(resume_path)
        resume_info["exists"] = True
        resume_info["size_kb"] = size_bytes / 1024
        resume_info["size_mb"] = size_bytes / (1024 * 1024)

    preview_contacts = session.pop("recent_csv_preview", None)
    return render_template("upload.html", resume_info=resume_info, preview_contacts=preview_contacts)


@app.route("/upload/resume", methods=["POST"])
def upload_resume_route():
    """Handles PDF resume upload and enforces > 1MB warning check."""
    if "resume_file" not in request.files:
        flash("No file part in the request.", "danger")
        return redirect(url_for("upload_page"))

    file = request.files["resume_file"]
    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("upload_page"))

    if not file.filename.lower().endswith(".pdf"):
        flash("Only PDF files are supported for resume attachments.", "danger")
        return redirect(url_for("upload_page"))

    filename = secure_filename(file.filename)
    if not filename:
        filename = "resume.pdf"

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    # Update setting in DB
    db.set_setting("resume_filename", filename)

    size_kb = os.path.getsize(save_path) / 1024
    if size_kb > 1024:
        flash(
            f"Resume '{filename}' uploaded successfully ({size_kb / 1024:.2f} MB), "
            "but notice: file exceeds 1 MB limit recommendation for optimal email deliverability.",
            "warning"
        )
    else:
        flash(f"Resume '{filename}' uploaded successfully ({size_kb:.1f} KB).", "success")

    return redirect(url_for("upload_page"))


@app.route("/download/resume")
def download_resume():
    """Serves the currently active resume PDF."""
    filename = db.get_setting("resume_filename", "sample_resume.pdf")
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


def parse_csv_stream(stream_or_text):
    """
    Parses CSV content, identifies name, email, company columns (case-insensitive),
    validates emails and returns (valid_list, duplicate_count, invalid_count).
    """
    if isinstance(stream_or_text, str):
        csv_file = io.StringIO(stream_or_text)
    else:
        csv_file = io.StringIO(stream_or_text.read().decode("utf-8", errors="ignore"))

    reader = csv.reader(csv_file)
    headers = next(reader, None)
    if not headers:
        return [], 0, 0, "CSV file is completely empty."

    # Map headers
    col_map = {}
    for idx, h in enumerate(headers):
        clean_h = h.strip().lower().replace(" ", "_")
        if clean_h in ("name", "hr_name", "contact_name", "candidate_name"):
            col_map["name"] = idx
        elif clean_h in ("email", "mail", "email_address", "hr_email"):
            col_map["email"] = idx
        elif clean_h in ("company", "company_name", "organization", "firm"):
            col_map["company"] = idx

    if "email" not in col_map:
        return [], 0, 0, "Missing required column 'email'. Headers found: " + ", ".join(headers)

    valid_contacts = []
    invalid_count = 0

    for row in reader:
        if not row:
            continue
        # Extract values
        email = row[col_map["email"]].strip().lower() if col_map.get("email") is not None and len(row) > col_map["email"] else ""
        name = row[col_map["name"]].strip() if col_map.get("name") is not None and len(row) > col_map["name"] else ""
        company = row[col_map["company"]].strip() if col_map.get("company") is not None and len(row) > col_map["company"] else ""

        if not mailer.is_valid_email(email):
            invalid_count += 1
            continue

        valid_contacts.append({
            "name": name or "Hiring Team",
            "email": email,
            "company": company or "your team"
        })

    added_count, duplicate_count, db_error_count = db.import_contacts(valid_contacts)
    return valid_contacts, added_count, duplicate_count, invalid_count


@app.route("/upload/csv", methods=["POST"])
def upload_csv_route():
    """Handles CSV file upload, duplicate dropping, validation, and previews."""
    if "csv_file" not in request.files:
        flash("No CSV file selected.", "danger")
        return redirect(url_for("upload_page"))

    file = request.files["csv_file"]
    if not file or file.filename == "":
        flash("No file selected.", "danger")
        return redirect(url_for("upload_page"))

    try:
        contacts, added, dups, invalid = parse_csv_stream(file.stream)
        if isinstance(invalid, str):  # Error message returned
            flash(invalid, "danger")
            return redirect(url_for("upload_page"))

        session["recent_csv_preview"] = contacts[:25]
        flash(
            f"CSV Processed: {added} new contacts added, {dups} duplicate emails skipped, "
            f"{invalid} invalid emails discarded. Choose dispatch options below!",
            "success"
        )
    except Exception as e:
        flash(f"Error parsing CSV file: {str(e)}", "danger")

    return redirect(url_for("pipeline_page"))


@app.route("/upload/csv-paste", methods=["POST"])
def paste_csv_route():
    """Handles raw pasted CSV text."""
    csv_text = request.form.get("csv_text", "").strip()
    if not csv_text:
        flash("Pasted CSV text was empty.", "warning")
        return redirect(url_for("upload_page"))

    try:
        contacts, added, dups, invalid = parse_csv_stream(csv_text)
        if isinstance(invalid, str):
            flash(invalid, "danger")
            return redirect(url_for("upload_page"))

        session["recent_csv_preview"] = contacts[:25]
        flash(
            f"Pasted CSV Processed: {added} new contacts added, {dups} duplicates skipped, "
            f"{invalid} invalid discarded. Choose dispatch options below!",
            "success"
        )
    except Exception as e:
        flash(f"Error parsing pasted CSV: {str(e)}", "danger")

    return redirect(url_for("pipeline_page"))


@app.route("/pipeline")
def pipeline_page():
    """Guided Outreach Pipeline: Template/Resume -> CSV -> Batch vs Individual -> Send Now or Schedule."""
    pending = db.get_pending_contacts()
    all_contacts = db.get_all_contacts()
    template_record = db.get_template("initial")
    resume_path = mailer.get_resume_path()
    resume_filename = os.path.basename(resume_path) if resume_path else None
    resume_size = round(os.path.getsize(resume_path) / 1024, 1) if resume_path else 0
    daily_cap = db.get_setting("daily_cap", "30")

    selected_contact = pending[0] if pending else (all_contacts[0] if all_contacts else None)
    rendered_subject = ""
    rendered_html = ""
    if selected_contact:
        rendered_subject, _, rendered_html = mailer.render_email(
            template_record["subject"], template_record["body"], selected_contact["name"], selected_contact["company"]
        )

    return render_template(
        "pipeline.html",
        pending_contacts=pending,
        all_contacts=all_contacts,
        template=template_record,
        resume_filename=resume_filename,
        resume_size=resume_size,
        daily_cap=daily_cap,
        selected_contact=selected_contact,
        rendered_subject=rendered_subject,
        rendered_html=rendered_html
    )


@app.route("/pipeline/dispatch", methods=["POST"])
def pipeline_dispatch():
    """Handles dispatch from the pipeline wizard: batch or individual, now or scheduled."""
    send_mode = request.form.get("send_mode", "batch")
    timing_mode = request.form.get("timing_mode", "now")
    schedule_datetime = request.form.get("schedule_datetime", "").strip()
    contact_id = request.form.get("contact_id")

    if send_mode == "batch":
        if timing_mode == "now":
            started, msg = mailer.queue_manager.start(mode="initial")
            flash(msg, "success" if started else "warning")
            return redirect(url_for("dashboard"))
        else:  # schedule batch
            if not schedule_datetime:
                flash("Please pick a date and time to schedule the batch.", "warning")
                return redirect(url_for("pipeline_page"))
            ok, msg = scheduler.schedule_one_off_batch(schedule_datetime, mode="initial")
            flash(msg, "success" if ok else "danger")
            return redirect(url_for("dashboard"))
    else:  # individual
        if not contact_id or not contact_id.isdigit():
            flash("Please select an HR contact from the dropdown.", "warning")
            return redirect(url_for("pipeline_page"))
        contact_id = int(contact_id)
        if timing_mode == "now":
            contact = db.get_contact_by_id(contact_id)
            if not contact:
                flash("Contact not found.", "danger")
                return redirect(url_for("pipeline_page"))
            dry_run = db.get_setting("dry_run", "0") == "1"
            template_record = db.get_template("initial")
            resume_path = mailer.get_resume_path()
            subj, plain, html = mailer.render_email(
                template_record["subject"], template_record["body"], contact["name"], contact["company"]
            )
            success, msg = mailer.send_email_smtp(
                recipient_email=contact["email"],
                subject=subj,
                plain_text=plain,
                html_text=html,
                resume_path=resume_path,
                dry_run=dry_run
            )
            if success:
                db.update_contact_status(contact_id, "dry_run" if dry_run else "sent")
                db.log_email(contact["email"], "initial", "dry_run" if dry_run else "success", msg, contact_id=contact_id)
                flash(f"Email sent successfully to {contact['email']}!", "success")
            else:
                db.update_contact_status(contact_id, "failed", error=msg)
                db.log_email(contact["email"], "initial", "failed", msg, contact_id=contact_id)
                flash(f"Failed sending to {contact['email']}: {msg}", "danger")
            return redirect(url_for("dashboard"))
        else:  # schedule individual
            if not schedule_datetime:
                flash("Please pick a date and time for this contact.", "warning")
                return redirect(url_for("pipeline_page"))
            ok, msg = scheduler.schedule_one_off_contact_job(contact_id, schedule_datetime)
            flash(msg, "success" if ok else "danger")
            return redirect(url_for("dashboard"))


@app.route("/api/contacts/<int:contact_id>/preview")
def api_contact_preview(contact_id):
    """Returns dynamic rendered preview data for a specific contact."""
    contact = db.get_contact_by_id(contact_id)
    if not contact:
        return jsonify({"success": False, "message": "Contact not found."}), 404
    template_record = db.get_template("initial")
    subj, plain, html = mailer.render_email(
        template_record["subject"], template_record["body"], contact["name"], contact["company"]
    )
    return jsonify({
        "success": True,
        "name": contact["name"],
        "email": contact["email"],
        "company": contact["company"],
        "subject": subj,
        "html": html
    })


@app.route("/templates")
def templates_page():
    """Template and Queue Settings configuration page."""
    initial_template = db.get_template("initial")
    follow_up_template = db.get_template("follow_up")
    settings = db.get_all_settings()
    return render_template(
        "templates_edit.html",
        initial_template=initial_template,
        follow_up_template=follow_up_template,
        settings=settings
    )


@app.route("/templates/save", methods=["POST"])
def save_templates_route():
    """Saves updated templates and safety settings to DB."""
    try:
        # Templates
        initial_sub = request.form.get("initial_subject", "").strip()
        initial_body = request.form.get("initial_body", "").strip()
        db.save_template("initial", initial_sub, initial_body)

        follow_sub = request.form.get("follow_up_subject", "").strip()
        follow_body = request.form.get("follow_up_body", "").strip()
        db.save_template("follow_up", follow_sub, follow_body)

        # Settings
        db.set_setting("daily_cap", request.form.get("daily_cap", "30"))
        db.set_setting("min_delay", request.form.get("min_delay", "30"))
        db.set_setting("max_delay", request.form.get("max_delay", "90"))
        db.set_setting("follow_up_days", request.form.get("follow_up_days", "5"))
        db.set_setting("sender_name", request.form.get("sender_name", "Danish Jain"))

        flash("Templates and configuration settings updated successfully.", "success")
    except Exception as e:
        flash(f"Error saving settings: {str(e)}", "danger")

    return redirect(url_for("templates_page"))


@app.route("/preview")
@app.route("/preview/<int:contact_id>")
def preview_page(contact_id=None):
    """Renders the email for any HR row before sending, displaying plain and HTML versions."""
    mode = request.args.get("mode", "initial")
    req_contact_id = request.args.get("contact_id")
    if req_contact_id and req_contact_id.isdigit():
        contact_id = int(req_contact_id)

    contacts = db.get_all_contacts()
    selected_contact = None

    if contact_id:
        selected_contact = db.get_contact_by_id(contact_id)

    if not selected_contact and contacts:
        selected_contact = contacts[0]

    template_record = db.get_template(mode) or db.get_template("initial")

    sample_name = selected_contact["name"] if selected_contact else "Sarah Connor"
    sample_company = selected_contact["company"] if selected_contact else "Acme Corp"

    rendered_subject, rendered_plain, rendered_html = mailer.render_email(
        template_record["subject"], template_record["body"], sample_name, sample_company
    )

    resume_path = mailer.get_resume_path()
    resume_filename = os.path.basename(resume_path) if resume_path else "No attachment"
    resume_size = round(os.path.getsize(resume_path) / 1024, 1) if resume_path else 0
    sender_name = db.get_setting("sender_name", "Danish Jain")

    return render_template(
        "preview.html",
        contacts=contacts,
        selected_contact=selected_contact,
        mode=mode,
        rendered_subject=rendered_subject,
        rendered_plain=rendered_plain,
        rendered_html=rendered_html,
        resume_path=resume_path,
        resume_filename=resume_filename,
        resume_size=resume_size,
        sender_name=sender_name
    )


@app.route("/scheduler")
def scheduler_page():
    """APScheduler management page: schedule batch and list active jobs."""
    jobs = scheduler.list_scheduled_jobs()
    return render_template("scheduler.html", jobs=jobs)


@app.route("/scheduler/date", methods=["POST"])
def schedule_date_route():
    """Schedules a one-off batch at a specific date and time."""
    run_datetime = request.form.get("run_datetime", "").strip()
    mode = request.form.get("mode", "initial")
    if not run_datetime:
        flash("Please provide a valid date and time.", "warning")
        return redirect(url_for("scheduler_page"))

    ok, msg = scheduler.schedule_one_off_batch(run_datetime, mode=mode)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("scheduler_page"))


@app.route("/scheduler/daily", methods=["POST"])
def schedule_daily_route():
    """Schedules a daily recurring batch at HH:MM."""
    daily_time = request.form.get("daily_time", "").strip()
    mode = request.form.get("mode", "initial")
    if not daily_time:
        flash("Please provide a valid daily time.", "warning")
        return redirect(url_for("scheduler_page"))

    ok, msg = scheduler.schedule_daily_batch(daily_time, mode=mode)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("scheduler_page"))


@app.route("/scheduler/cancel/<job_id>", methods=["POST"])
def cancel_job_route(job_id):
    """Cancels a scheduled job."""
    ok, msg = scheduler.cancel_job(job_id)
    flash(msg, "info" if ok else "danger")
    return redirect(url_for("scheduler_page"))


@app.route("/logs/clear", methods=["POST"])
def clear_logs_route():
    """Clears recent activity logs."""
    db.clear_logs()
    flash("Activity logs cleared.", "info")
    return redirect(url_for("dashboard"))


@app.route("/export/csv")
def export_csv_route():
    """Exports all contacts and outreach history into a downloadable CSV report."""
    contacts = db.get_all_contacts()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "ID", "Name", "Email", "Company", "Status", "Sent At",
        "Follow-up Status", "Follow-up Sent At", "Replied", "Do Not Contact", "Error"
    ])
    for c in contacts:
        writer.writerow([
            c["id"], c["name"], c["email"], c["company"],
            c["status"], c["sent_at"] or "",
            c["follow_up_status"] or "pending", c["follow_up_sent_at"] or "",
            "Yes" if c["replied"] else "No",
            "Yes" if c["do_not_contact"] else "No",
            c["error"] or ""
        ])

    return app.response_class(
        output.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=hr_outreach_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"}
    )


# ==============================================================================
# REST / JSON API Endpoints
# ==============================================================================

@app.route("/api/send-test", methods=["POST"])
def api_send_test():
    """
    Sends 1 test mail directly to danishsjain@gmail.com using the active template and resume.
    """
    target_email = "danishsjain@gmail.com"
    template_record = db.get_template("initial")
    dry_run = db.get_setting("dry_run", "0") == "1"

    rendered_subject, rendered_plain, rendered_html = mailer.render_email(
        template_record["subject"], template_record["body"], "Test Candidate", "Sample Target Co"
    )
    resume_path = mailer.get_resume_path()

    success, message = mailer.send_email_smtp(
        recipient_email=target_email,
        subject=f"[TEST] {rendered_subject}",
        plain_text=rendered_plain,
        html_text=rendered_html,
        resume_path=resume_path,
        dry_run=dry_run
    )

    db.log_email(
        target_email,
        "test",
        "success" if success else "failed",
        message
    )

    return jsonify({"success": success, "message": message})


@app.route("/api/queue/start", methods=["POST"])
def api_queue_start():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "initial")
    success, msg = mailer.queue_manager.start(mode=mode)
    return jsonify({"success": success, "message": msg})


@app.route("/api/queue/stop", methods=["POST"])
def api_queue_stop():
    success, msg = mailer.queue_manager.stop()
    return jsonify({"success": success, "message": msg})


@app.route("/api/queue/status")
def api_queue_status():
    return jsonify(mailer.queue_manager.get_status())


@app.route("/api/settings/toggle-dry-run", methods=["POST"])
def api_toggle_dry_run():
    data = request.get_json(silent=True) or {}
    new_val = "1" if data.get("enabled") else "0"
    db.set_setting("dry_run", new_val)
    mode_text = "ENABLED (no emails will be sent)" if new_val == "1" else "DISABLED (live emails will be sent via Gmail SMTP)"
    return jsonify({"success": True, "message": f"Dry-Run mode is now {mode_text}."})


@app.route("/api/contacts/<int:contact_id>/toggle", methods=["POST"])
def api_toggle_contact(contact_id):
    data = request.get_json(silent=True) or {}
    flag = data.get("flag")
    val = data.get("value", False)
    ok = db.toggle_contact_flag(contact_id, flag, val)
    return jsonify({"success": ok, "message": f"Updated {flag} to {val}."})


@app.route("/api/contacts/<int:contact_id>/send-now", methods=["POST"])
def api_send_contact_now(contact_id):
    contact = db.get_contact_by_id(contact_id)
    if not contact:
        return jsonify({"success": False, "message": "Contact not found."}), 404

    dry_run = db.get_setting("dry_run", "0") == "1"
    template_record = db.get_template("initial")
    resume_path = mailer.get_resume_path()

    rendered_subject, rendered_plain, rendered_html = mailer.render_email(
        template_record["subject"], template_record["body"], contact["name"], contact["company"]
    )

    success, msg = mailer.send_email_smtp(
        recipient_email=contact["email"],
        subject=rendered_subject,
        plain_text=rendered_plain,
        html_text=rendered_html,
        resume_path=resume_path,
        dry_run=dry_run
    )

    if success:
        db.update_contact_status(contact_id, "dry_run" if dry_run else "sent", error=None)
        db.log_email(contact["email"], "initial", "dry_run" if dry_run else "success", msg, contact_id=contact_id)
    else:
        db.update_contact_status(contact_id, "failed", error=msg)
        db.log_email(contact["email"], "initial", "failed", msg, contact_id=contact_id)

    return jsonify({"success": success, "message": msg})


@app.route("/api/contacts/<int:contact_id>/delete", methods=["POST"])
def api_delete_contact(contact_id):
    db.delete_contact(contact_id)
    return jsonify({"success": True, "message": "Contact removed."})


@app.route("/api/contacts/retry-failed", methods=["POST"])
def api_retry_failed():
    count = db.reset_failed_contacts()
    return jsonify({"success": True, "message": f"Reset {count} failed contacts back to pending."})


# ==============================================================================
# Main Entry Point
# ==============================================================================

if __name__ == "__main__":
    # Initialize DB & Scheduler
    db.init_db()
    scheduler.init_scheduler()

    port = int(os.environ.get("PORT", 5000))
    print("\n=======================================================")
    print(f"🚀 HR Cold Mailer is starting on http://localhost:{port}")
    print("=======================================================\n")

    # Run Flask server
    app.run(host="0.0.0.0", port=port, debug=False)

