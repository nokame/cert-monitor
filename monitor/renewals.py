import os
import traceback
from datetime import datetime, timezone

from cryptography.hazmat.primitives import serialization

from . import certchecker
from .acme import certs, challenges, client, iis


def get_or_create_account(config):
    key = certs.load_or_create_key(config["acme_account_key_path"])
    kid_path = config["acme_account_key_path"] + ".kid"
    kid = None
    if os.path.exists(kid_path):
        with open(kid_path, encoding="utf-8") as fh:
            kid = fh.read().strip() or None
    return key, kid, kid_path


def save_kid(kid_path, kid):
    with open(kid_path, "w", encoding="utf-8") as fh:
        fh.write(kid)


def _handler_factory(config, task):
    mode = task["challenge_mode"] or config["acme_challenge_mode"]

    if mode == "standalone":
        return lambda: challenges.StandaloneHandler(port=config["acme_standalone_port"])

    webroot = task["webroot"] or config["acme_webroot"]
    if not webroot:
        raise ValueError("Mode webroot : le chemin du répertoire racine IIS (webroot) est requis.")

    return lambda: challenges.WebrootHandler(webroot)


def _load_key_pem(key_pem):
    return serialization.load_pem_private_key(key_pem, password=None)


def _write(path, data):
    with open(path, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def _finish(db, task_id, status, not_after, days_left, log):
    db.execute(
        "UPDATE renewal_tasks SET status = ?, last_run_at = datetime('now'),"
        " cert_not_after = ?, days_left = ?, log = ? WHERE id = ?",
        (status, not_after, days_left, "\n".join(log), task_id),
    )


def run_task(db, config, task, force=False):
    task_id = task["id"]
    domain = task["domain"]
    sans = [s.strip() for s in (task["san"] or "").split(",") if s.strip()]
    domains = [domain] + [s for s in sans if s != domain]
    log = []

    def note(msg):
        log.append(str(msg))

    check = certchecker.check_site({"host": domain, "port": 443})
    days_left = check.get("days_left")
    if check["status"] == "ok":
        note(f"Certificat actuel : expire le {check['not_after']} ({days_left} jours restants).")
        if not force and days_left is not None and days_left > config["renew_before_days"]:
            note(f"Seuil de renouvellement J-{config['renew_before_days']} non atteint : aucune action.")
            _finish(db, task_id, "up_to_date", check["not_after"], days_left, log)
            return {"status": "up_to_date", "log": log}
    elif not force:
        note(f"Certificat actuel non vérifiable ({check['error']}). Utilisez « Renouveler maintenant » pour forcer.")
        _finish(db, task_id, "error", None, None, log)
        return {"status": "error", "log": log}
    else:
        note(f"Certificat actuel non vérifiable ({check['error']}). Renouvellement forcé.")

    try:
        note(f"Serveur ACME : {config['acme_directory_url']}")
        key, kid, kid_path = get_or_create_account(config)
        acme = client.AcmeClient(config["acme_directory_url"], key, email=config["acme_email"])
        if kid:
            acme.set_kid(kid)
        note(f"Compte ACME : {'réutilisé' if kid else 'nouveau'}")

        note(f"Demande de certificat pour : {', '.join(domains)} (défi http-01)")
        chain, key_pem = acme.obtain_certificate(domains, _handler_factory(config, task))
        if not kid and acme.kid:
            save_kid(kid_path, acme.kid)
        note("Certificat émis par le serveur ACME.")

        leaf, _ = certs.parse_chain(chain)
        not_after = certs.cert_not_after(leaf).strftime("%Y-%m-%d")
        note(f"Nouveau certificat : expire le {not_after}.")

        order_key = _load_key_pem(key_pem)
        pfx_password = config["acme_pfx_password"]
        pfx_bytes = certs.build_pkcs12(chain, order_key, pfx_password)

        site_name = (task["iis_site"] or "").strip()
        if site_name:
            if iis.is_windows():
                thumb = iis.install_certificate_iis(pfx_bytes, pfx_password, domain, 443, site_name)
                note(f"Certificat installé dans IIS (site « {site_name} », empreinte {thumb}).")
            else:
                note("Site IIS renseigné mais hôte non-Windows : installation IIS ignorée.")

        out_dir = config["acme_output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        base = domain.replace("*", "_")
        _write(os.path.join(out_dir, f"{base}.pem"), chain)
        _write(os.path.join(out_dir, f"{base}.key"), key_pem)
        _write(os.path.join(out_dir, f"{base}.pfx"), pfx_bytes)
        note(f"Fichiers écrits dans {out_dir} ({base}.pem/.key/.pfx).")

        new_days_left = (certs.cert_not_after(leaf) - datetime.now(timezone.utc)).days
        _finish(db, task_id, "renewed", not_after, new_days_left, log)
        return {"status": "renewed", "log": log}
    except Exception as exc:
        note(f"ERREUR : {exc}")
        _finish(db, task_id, "error", check.get("not_after"), days_left, log)
        return {"status": "error", "log": log}


def run_due(db, config, force=False):
    rows = db.execute("SELECT * FROM renewal_tasks WHERE enabled = 1").fetchall()
    results = []
    for row in rows:
        task = dict(row)
        try:
            results.append({"id": task["id"], **run_task(db, config, task, force=force)})
        except Exception as exc:
            results.append({"id": task["id"], "status": "error", "log": [str(exc)]})
    return results
