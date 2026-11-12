import os

import sqlite3

from flask import Flask, g, jsonify, render_template, request

from monitor import certchecker
from monitor.config import load_config
from monitor.mailer import Mailer
from monitor.scheduler import Scheduler

app = Flask(__name__)
config = load_config()
mailer = Mailer(config)
scheduler = Scheduler(config, mailer)

DB_PATH = config["db_path"]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            host TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 443,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_checked_at TEXT,
            last_not_after TEXT,
            last_days_left INTEGER,
            last_status TEXT,
            last_error TEXT
        );

        CREATE TABLE IF NOT EXISTS checks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            checked_at TEXT NOT NULL DEFAULT (datetime('now')),
            not_after TEXT,
            days_left INTEGER,
            status TEXT NOT NULL,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL REFERENCES sites(id) ON DELETE CASCADE,
            threshold_days INTEGER NOT NULL,
            sent_at TEXT NOT NULL DEFAULT (datetime('now')),
            days_left_at_send INTEGER NOT NULL,
            UNIQUE(site_id, threshold_days)
        );
        """
    )
    db.commit()
    db.close()


init_db()


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/sites")
def list_sites():
    rows = get_db().execute(
        "SELECT * FROM sites ORDER BY"
        " CASE WHEN last_status IS NULL THEN 0"
        " WHEN last_status = 'error' THEN 1"
        " WHEN last_status = 'expired' THEN 2"
        " WHEN last_status = 'critical' THEN 3"
        " WHEN last_status = 'warning' THEN 4"
        " ELSE 5 END, name"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/sites")
def create_site():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    host = (data.get("host") or "").strip().replace("https://", "").replace("http://", "").rstrip("/")
    port = data.get("port") or 443
    if not name or not host:
        return jsonify({"error": "Nom et hôte obligatoires."}), 400
    try:
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Port invalide."}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO sites (name, host, port, enabled) VALUES (?, ?, ?, ?)",
        (name, host, port, 1 if data.get("enabled", True) else 0),
    )
    db.commit()
    row = db.execute("SELECT * FROM sites WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.put("/api/sites/<int:site_id>")
def update_site(site_id):
    data = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Site introuvable."}), 404
    name = (data.get("name") or row["name"]).strip()
    host = (data.get("host") or row["host"]).strip().replace("https://", "").replace("http://", "").rstrip("/")
    try:
        port = int(data.get("port", row["port"]))
        if not 1 <= port <= 65535:
            raise ValueError
    except (TypeError, ValueError):
        return jsonify({"error": "Port invalide."}), 400
    enabled = 1 if data.get("enabled", bool(row["enabled"])) else 0
    host_changed = host != row["host"] or port != row["port"]
    db.execute(
        "UPDATE sites SET name = ?, host = ?, port = ?, enabled = ? WHERE id = ?",
        (name, host, port, enabled, site_id),
    )
    if host_changed:
        db.execute(
            "UPDATE sites SET last_status = NULL, last_days_left = NULL,"
            " last_not_after = NULL, last_error = NULL WHERE id = ?",
            (site_id,),
        )
        db.execute("DELETE FROM notifications WHERE site_id = ?", (site_id,))
    db.commit()
    updated = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return jsonify(dict(updated))


@app.delete("/api/sites/<int:site_id>")
def delete_site(site_id):
    db = get_db()
    cur = db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Site introuvable."}), 404
    return jsonify({"ok": True})


@app.post("/api/sites/<int:site_id>/check")
def check_site(site_id):
    db = get_db()
    row = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    if row is None:
        return jsonify({"error": "Site introuvable."}), 404
    result = certchecker.check_site(dict(row))
    certchecker.save_result(get_db(), site_id, result, config)
    certchecker.evaluate_and_notify(get_db(), config, mailer, site_id, result)
    db.commit()
    updated = db.execute("SELECT * FROM sites WHERE id = ?", (site_id,)).fetchone()
    return jsonify({"result": result, "site": dict(updated)})


@app.post("/api/check-all")
def check_all():
    db = get_db()
    rows = db.execute("SELECT * FROM sites WHERE enabled = 1").fetchall()
    results = []
    for row in rows:
        result = certchecker.check_site(dict(row))
        certchecker.save_result(db, row["id"], result, config)
        certchecker.evaluate_and_notify(db, config, mailer, row["id"], result)
        results.append({"site_id": row["id"], "status": result["status"]})
    db.commit()
    return jsonify({"checked": len(results), "results": results})


@app.get("/api/config")
def get_config():
    return jsonify(
        {
            "warn_days": config["warn_days"],
            "critical_days": config["critical_days"],
            "notify_days": config["notify_days"],
            "check_interval_hours": config["check_interval_hours"],
            "mail_configured": mailer.is_configured(),
        }
    )


scheduler.start()
