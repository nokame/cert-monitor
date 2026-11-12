import socket
import ssl
import tempfile
from datetime import datetime, timezone


def check_site(site):
    host = site["host"]
    port = int(site.get("port") or 443)
    try:
        pem = ssl.get_server_certificate((host, port), timeout=10)
    except (socket.gaierror, socket.timeout, ConnectionRefusedError, OSError, ssl.SSLError) as exc:
        return {"status": "error", "not_after": None, "days_left": None, "error": str(exc)}

    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as fh:
        fh.write(pem)
        path = fh.name
    try:
        cert = ssl._ssl._test_decode_cert(path)
    except (ssl.SSLError, ValueError) as exc:
        return {"status": "error", "not_after": None, "days_left": None, "error": str(exc)}
    finally:
        import os

        os.unlink(path)

    not_after = cert.get("notAfter")
    if not not_after:
        return {"status": "error", "not_after": None, "days_left": None, "error": "Date d'expiration absente du certificat."}
    expiry = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    days_left = (expiry - datetime.now(timezone.utc)).days
    return {"status": "ok", "not_after": expiry.strftime("%Y-%m-%d"), "days_left": days_left, "error": None}


def status_for(days_left, config):
    if days_left is None:
        return "error"
    if days_left < 0:
        return "expired"
    if days_left <= config["critical_days"]:
        return "critical"
    if days_left <= config["warn_days"]:
        return "warning"
    return "ok"


def save_result(db, site_id, result, config):
    status = status_for(result["days_left"], config) if result["status"] == "ok" else result["status"]
    db.execute(
        "INSERT INTO checks (site_id, not_after, days_left, status, error) VALUES (?, ?, ?, ?, ?)",
        (site_id, result["not_after"], result["days_left"], status, result["error"]),
    )
    db.execute(
        "UPDATE sites SET last_checked_at = datetime('now'), last_not_after = ?,"
        " last_days_left = ?, last_status = ?, last_error = ? WHERE id = ?",
        (result["not_after"], result["days_left"], status, result["error"], site_id),
    )


def evaluate_and_notify(db, config, mailer, site_id, result):
    if result["status"] != "ok" or result["days_left"] is None:
        return
    days_left = result["days_left"]
    site = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if site is None:
        return
    for threshold in config["notify_days"]:
        if days_left > threshold:
            continue
        already = db.execute(
            "SELECT 1 FROM notifications WHERE site_id = ? AND threshold_days = ?",
            (site_id, threshold),
        ).fetchone()
        if already:
            continue
        sent = mailer.send_alert(dict(site), result, threshold)
        if sent:
            db.execute(
                "INSERT OR IGNORE INTO notifications (site_id, threshold_days, days_left_at_send)"
                " VALUES (?, ?, ?)",
                (site_id, threshold, days_left),
            )
        break
