// HR Cold Mailer Client Scripts

function showToast(message, isError = false) {
    let toast = document.getElementById("toast");
    if (!toast) {
        toast = document.createElement("div");
        toast.id = "toast";
        document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.style.backgroundColor = isError ? "#ef4444" : "#10b981";
    toast.className = "show";
    setTimeout(() => {
        toast.className = toast.className.replace("show", "");
    }, 3000);
}

// Queue Poller
let pollInterval = null;

function updateQueueUI(data) {
    const indicator = document.getElementById("queue-status-indicator");
    const statusText = document.getElementById("queue-status-text");
    const messageText = document.getElementById("queue-message-text");
    const progressBar = document.getElementById("queue-progress-bar");
    const progressCount = document.getElementById("queue-progress-count");
    const startBtn = document.getElementById("btn-start-queue");
    const stopBtn = document.getElementById("btn-stop-queue");

    if (!indicator || !statusText) return;

    if (data.is_running) {
        indicator.className = "status-indicator active";
        statusText.textContent = `Running (${data.mode})`;
        if (startBtn) startBtn.disabled = true;
        if (stopBtn) stopBtn.disabled = false;
    } else {
        indicator.className = "status-indicator";
        statusText.textContent = data.status.charAt(0).toUpperCase() + data.status.slice(1);
        if (startBtn) startBtn.disabled = false;
        if (stopBtn) stopBtn.disabled = true;
    }

    if (messageText) messageText.textContent = data.message || "Ready";

    const total = data.total || 0;
    const processed = data.processed || 0;
    const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;

    if (progressBar) progressBar.style.width = pct + "%";
    if (progressCount) progressCount.textContent = `${processed} / ${total} (${pct}%)`;

    // Today's cap update
    const todaySentEl = document.getElementById("sent-today-count");
    if (todaySentEl && data.sent_today !== undefined) {
        todaySentEl.textContent = `${data.sent_today} / ${data.daily_cap}`;
    }
}

async function fetchQueueStatus() {
    try {
        const res = await fetch("/api/queue/status");
        if (res.ok) {
            const data = await res.json();
            updateQueueUI(data);
            if (data.is_running && !pollInterval) {
                pollInterval = setInterval(fetchQueueStatus, 2000);
            } else if (!data.is_running && pollInterval) {
                clearInterval(pollInterval);
                pollInterval = null;
            }
        }
    } catch (e) {
        console.error("Queue status poll error:", e);
    }
}

async function startQueue(mode = "initial") {
    try {
        const res = await fetch("/api/queue/start", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ mode: mode })
        });
        const result = await res.json();
        if (result.success) {
            showToast(result.message);
            fetchQueueStatus();
            if (!pollInterval) {
                pollInterval = setInterval(fetchQueueStatus, 2000);
            }
        } else {
            showToast(result.message, true);
        }
    } catch (e) {
        showToast("Error starting queue: " + e.message, true);
    }
}

async function stopQueue() {
    try {
        const res = await fetch("/api/queue/stop", { method: "POST" });
        const result = await res.json();
        showToast(result.message, !result.success);
        fetchQueueStatus();
    } catch (e) {
        showToast("Error stopping queue: " + e.message, true);
    }
}

async function toggleDryRun(checkbox) {
    try {
        const res = await fetch("/api/settings/toggle-dry-run", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: checkbox.checked })
        });
        const result = await res.json();
        showToast(result.message);
        setTimeout(() => window.location.reload(), 600);
    } catch (e) {
        showToast("Error toggling dry run: " + e.message, true);
    }
}

async function sendTestEmail() {
    const btn = document.getElementById("btn-test-mail");
    const originalText = btn ? btn.innerHTML : "Send Test";
    if (btn) {
        btn.disabled = true;
        btn.innerHTML = `<span style="display:inline-block;animation:spin 1s linear infinite;">⏳</span> Sending Test...`;
    }

    try {
        const res = await fetch("/api/send-test", { method: "POST" });
        const result = await res.json();

        if (result.success) {
            openModal("Test Email Success", `<div class="alert alert-success">${result.message}</div>`);
            showToast("Test email sent successfully!");
        } else {
            openModal("Test Email Failed", `
                <div class="alert alert-danger" style="margin-bottom: 1rem;">
                    <strong>Error:</strong><br>${result.message}
                </div>
                <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:12px; font-size:13px;">
                    <strong>Setup Instructions for Gmail SMTP:</strong>
                    <ol style="margin-left: 20px; margin-top: 6px;">
                        <li>Open your Google Account Security: <a href="https://myaccount.google.com/security" target="_blank">Google Security</a></li>
                        <li>Verify that <strong>2-Step Verification</strong> is switched ON.</li>
                        <li>Search for or open <a href="https://myaccount.google.com/apppasswords" target="_blank">App Passwords</a>.</li>
                        <li>Create a new App Password (name it "Cold Mailer") and copy the 16 characters.</li>
                        <li>Paste it in your <code>.env</code> file as <code>GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx</code>.</li>
                        <li>Never share or commit this password.</li>
                    </ol>
                </div>
            `);
        }
    } catch (e) {
        showToast("Test mail error: " + e.message, true);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerHTML = originalText;
        }
    }
}

async function toggleContactFlag(contactId, flagName, isChecked) {
    try {
        const res = await fetch(`/api/contacts/${contactId}/toggle`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ flag: flagName, value: isChecked })
        });
        const result = await res.json();
        if (result.success) {
            showToast(result.message);
        } else {
            showToast(result.message, true);
        }
    } catch (e) {
        showToast("Error updating flag: " + e.message, true);
    }
}

async function retryFailedContacts() {
    try {
        const res = await fetch("/api/contacts/retry-failed", { method: "POST" });
        const result = await res.json();
        showToast(result.message);
        setTimeout(() => window.location.reload(), 800);
    } catch (e) {
        showToast("Error resetting failed: " + e.message, true);
    }
}

async function sendContactNow(contactId) {
    if (!confirm("Are you sure you want to send this email right now?")) return;
    try {
        const res = await fetch(`/api/contacts/${contactId}/send-now`, { method: "POST" });
        const result = await res.json();
        if (result.success) {
            showToast(result.message);
            setTimeout(() => window.location.reload(), 1000);
        } else {
            openModal("Sending Error", `<div class="alert alert-danger">${result.message}</div>`);
        }
    } catch (e) {
        showToast("Error sending: " + e.message, true);
    }
}

async function deleteContact(contactId) {
    if (!confirm("Delete this contact from the list?")) return;
    try {
        const res = await fetch(`/api/contacts/${contactId}/delete`, { method: "POST" });
        const result = await res.json();
        showToast(result.message);
        const row = document.getElementById(`contact-row-${contactId}`);
        if (row) row.remove();
    } catch (e) {
        showToast("Error deleting: " + e.message, true);
    }
}

// Modal logic
function openModal(title, contentHtml) {
    let modal = document.getElementById("dynamic-modal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "dynamic-modal";
        modal.className = "modal-overlay";
        modal.innerHTML = `
            <div class="modal-content">
                <div class="modal-header">
                    <h3 id="modal-title" style="font-size:1.15rem; font-weight:600;"></h3>
                    <button class="modal-close" onclick="closeModal()">&times;</button>
                </div>
                <div id="modal-body"></div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.addEventListener("click", (e) => {
            if (e.target === modal) closeModal();
        });
    }
    document.getElementById("modal-title").textContent = title;
    document.getElementById("modal-body").innerHTML = contentHtml;
    modal.classList.add("active");
}

function closeModal() {
    const modal = document.getElementById("dynamic-modal");
    if (modal) modal.classList.remove("active");
}

// Client-side table search filter
function filterContactsTable() {
    const input = document.getElementById("search-input");
    if (!input) return;
    const filter = input.value.toLowerCase();
    const rows = document.querySelectorAll("#contacts-table tbody tr");
    rows.forEach(row => {
        const text = row.textContent.toLowerCase();
        row.style.display = text.includes(filter) ? "" : "none";
    });
}

// Auto-run status check on page load
document.addEventListener("DOMContentLoaded", () => {
    fetchQueueStatus();
});

