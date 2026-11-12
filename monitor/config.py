import os


def load_config():
    env = {}
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8-sig") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env.setdefault(key.strip(), value.strip().strip('"').strip("'"))

    def get(key, default=None):
        return os.environ.get(key) or env.get(key) or default

    def get_int(key, default):
        try:
            return int(get(key, default))
        except (TypeError, ValueError):
            return default

    notify_days = [int(d) for d in str(get("NOTIFY_DAYS", "30")).split(",") if d.strip().isdigit()]

    db_path = get("DB_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "monitor.db"))
    db_dir = os.path.dirname(os.path.abspath(db_path))
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    return {
        "db_path": db_path,
        "warn_days": get_int("WARN_DAYS", 30),
        "critical_days": get_int("CRITICAL_DAYS", 7),
        "notify_days": sorted(set(notify_days), reverse=True),
        "check_interval_hours": get_int("CHECK_INTERVAL_HOURS", 6),
        "smtp_host": get("SMTP_HOST"),
        "smtp_port": get_int("SMTP_PORT", 587),
        "smtp_user": get("SMTP_USER"),
        "smtp_password": get("SMTP_PASSWORD"),
        "smtp_use_tls": str(get("SMTP_USE_TLS", "true")).lower() in ("1", "true", "yes"),
        "mail_from": get("MAIL_FROM"),
        "mail_to": [a.strip() for a in str(get("MAIL_TO", "")).split(",") if a.strip()],
        "base_url": get("BASE_URL", "http://localhost:5000"),
        "acme_directory_url": get("ACME_DIRECTORY_URL", "https://acme-v02.api.letsencrypt.org/directory"),
        "acme_email": get("ACME_EMAIL"),
        "acme_account_key_path": get("ACME_ACCOUNT_KEY_PATH", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "acme_account.key")),
        "acme_output_dir": get("ACME_OUTPUT_DIR", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "certs")),
        "acme_challenge_mode": get("ACME_CHALLENGE_MODE", "webroot"),
        "acme_webroot": get("ACME_WEBROOT"),
        "acme_standalone_port": get_int("ACME_STANDALONE_PORT", 80),
        "acme_pfx_password": get("ACME_PFX_PASSWORD", "cert-monitor"),
        "renew_before_days": get_int("RENEW_BEFORE_DAYS", 30),
    }
