# Cert Monitor — Surveillance des certificats HTTPS

Outil de monitoring de la validité des certificats HTTPS avec :

- **Dashboard web** : ajout, modification, suppression des sites surveillés, statut en temps réel (OK / Attention / Critique / Expiré / Erreur).
- **Vérification automatique** des certificats toutes les 6 h (configurable) + vérification manuelle à la demande.
- **Notifications e-mail** à **J-30** avant expiration (seuils configurables, un seul e-mail par seuil et par certificat).

## Stack

- Python 3 + Flask (API + dashboard), SQLite (aucune base à installer), planificateur en thread interne.
- Frontend : page HTML/CSS/JS unique, sans build.

## Installation

```bash
cd cert-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # puis renseignez SMTP et destinataires
```

## Lancement

```bash
python run.py
# ou en production :
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

Le dashboard est disponible sur http://localhost:5000.

## Configuration (`.env`)

| Variable | Défaut | Description |
|---|---|---|
| `WARN_DAYS` | `30` | Seuil d'affichage « Attention » |
| `CRITICAL_DAYS` | `7` | Seuil d'affichage « Critique » |
| `NOTIFY_DAYS` | `30` | Seuils d'e-mail (ex. `30,15,7` pour J-30, J-15, J-7) |
| `CHECK_INTERVAL_HOURS` | `6` | Fréquence des vérifications automatiques |
| `SMTP_HOST` / `SMTP_PORT` | — | Serveur SMTP |
| `SMTP_USER` / `SMTP_PASSWORD` | — | Authentification SMTP |
| `SMTP_USE_TLS` | `true` | STARTTLS (ou SSL implicite si port 465) |
| `MAIL_FROM` / `MAIL_TO` | — | Expéditeur / destinataires (séparés par des virgules) |
| `BASE_URL` | `http://localhost:5000` | URL du dashboard dans les e-mails |

## API

| Méthode | Route | Description |
|---|---|---|
| `GET` | `/api/sites` | Liste des sites avec statut |
| `POST` | `/api/sites` | Ajouter un site `{name, host, port, enabled}` |
| `PUT` | `/api/sites/:id` | Modifier un site |
| `DELETE` | `/api/sites/:id` | Supprimer un site |
| `POST` | `/api/sites/:id/check` | Vérification manuelle d'un site |
| `POST` | `/api/check-all` | Vérifier tous les sites |
| `GET` | `/api/config` | Configuration courante |

## Fonctionnement des alertes

À chaque vérification, si le certificat expire dans `NOTIFY_DAYS` jours ou moins, un e-mail est envoyé (une seule fois par seuil et par certificat — pas de spam). Si le site est modifié (hôte/port), les alertes sont réarmées.

## Notes

- La base SQLite est créée automatiquement au premier démarrage (`data/monitor.db`).
- Pour un déploiement serveur, exécutez derrière nginx/Apache en HTTPS et gardez `.env` hors du contrôle de version.
