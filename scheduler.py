"""
Scheduler module for HR Cold Mailer.
Integrates APScheduler to handle one-off date/time triggers and recurring daily triggers.
"""

import uuid
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger

import db
import mailer

scheduler = BackgroundScheduler(daemon=True)


def scheduled_queue_worker(mode="initial"):
    """Triggered by APScheduler when a job fires."""
    print(f"[{datetime.now()}] APScheduler triggered batch for mode: {mode}")
    db.log_email("system", mode, "info", f"Scheduled job fired for batch mode: {mode}")
    started, msg = mailer.queue_manager.start(mode=mode)
    if not started:
        db.log_email("system", mode, "warning", f"Scheduled job could not start queue: {msg}")


def init_scheduler():
    """Initializes and starts APScheduler if not already running."""
    if not scheduler.running:
        scheduler.start()
        print("APScheduler started successfully.")


def schedule_one_off_batch(run_datetime_str, mode="initial"):
    """
    Schedules a one-off batch to run at a specific date and time.
    Format expected: 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM'
    """
    clean_str = run_datetime_str.replace("T", " ")
    if len(clean_str) == 16:
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M")
    else:
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")

    if dt <= datetime.now():
        return False, "Scheduled time must be in the future."

    job_id = f"job_date_{uuid.uuid4().hex[:8]}"
    job_name = f"One-off {mode.capitalize()} Batch at {dt.strftime('%Y-%m-%d %H:%M')}"

    scheduler.add_job(
        scheduled_queue_worker,
        trigger=DateTrigger(run_date=dt),
        args=[mode],
        id=job_id,
        name=job_name,
        replace_existing=True
    )

    db.record_scheduled_job(job_id, job_name, "date", dt.strftime("%Y-%m-%d %H:%M:%S"))
    return True, f"Successfully scheduled batch for {dt.strftime('%b %d, %Y at %I:%M %p')}."


def schedule_daily_batch(time_str, mode="initial"):
    """
    Schedules a daily recurring batch at HH:MM (e.g. '10:00').
    """
    try:
        parts = time_str.strip().split(":")
        hour = int(parts[0])
        minute = int(parts[1])
    except Exception:
        return False, "Invalid time format. Please use HH:MM (24-hour format, e.g. 10:00)."

    job_id = f"job_daily_{mode}_{hour:02d}_{minute:02d}"
    job_name = f"Daily {mode.capitalize()} Batch at {hour:02d}:{minute:02d}"

    scheduler.add_job(
        scheduled_queue_worker,
        trigger=CronTrigger(hour=hour, minute=minute),
        args=[mode],
        id=job_id,
        name=job_name,
        replace_existing=True
    )

    db.record_scheduled_job(job_id, job_name, "daily", f"{hour:02d}:{minute:02d}")
    return True, f"Successfully scheduled daily batch at {hour:02d}:{minute:02d}."


def list_scheduled_jobs():
    """Returns a list of all currently active jobs in APScheduler."""
    jobs_info = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time.strftime("%Y-%m-%d %I:%M:%S %p") if job.next_run_time else "None"
        trigger_str = str(job.trigger)
        if "date[" in trigger_str:
            trigger_type = "One-off"
        elif "cron[" in trigger_str:
            trigger_type = "Daily recurring"
        else:
            trigger_type = "Recurring"

        jobs_info.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": next_run,
            "trigger": trigger_str,
            "trigger_type": trigger_type
        })
    return jobs_info


def cancel_job(job_id):
    """Cancels a scheduled job by its id."""
    try:
        job = scheduler.get_job(job_id)
        if job:
            scheduler.remove_job(job_id)
            db.cancel_scheduled_job_record(job_id)
            return True, f"Scheduled job '{job.name}' cancelled."
        else:
            db.cancel_scheduled_job_record(job_id)
            return False, f"Job with ID '{job_id}' not found in active scheduler."
    except Exception as e:
        return False, f"Error cancelling job: {str(e)}"

