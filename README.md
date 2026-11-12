# Cert Monitor v2 — Surveillance et renouvellement automatique des certificats

Outil complet de gestion des certificats HTTPS :

- **Surveillance** : dashboard web (ajout / modification / suppression des sites), statut temps réel (OK / Attention ≤ J-30 / Critique ≤ J-7 / Expiré / Erreur), notifications e-mail à **J-30**.
- **Agent ACME (v2)** : client Let's Encrypt (RFC 8555) intégré, **renouvellement automatique** à J-30, défis **http-01** en mode **webroot IIS** ou **standalone** (port 80), installation automatique dans **IIS 10** (Windows Server 2019/2022) via PowerShell.
- **Gestion des tâches de renouvellement via le dashboard** : onglet dédié avec création, modification, suppression, renouvellement manuel (« ⚡ ») et journal détaillé de chaque exécution.

## Stack

- Python 3 + Flask (API + dashboard), SQLite, planificateur en thread interne.
- Client ACME maison (`monitor/acme/`) : JWS RS256, comptes, commandes, défis http-01, CSR — basé sur `cryptography` uniquement.
- Aucun build frontend : page HTML/CSS/JS unique.

## Installation

```bash
cd cert-monitor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # renseigner SMTP + ACME_EMAIL
```

## Lancement

```bash
python run.py
# ou en production :
waitress-serve --host=0.0.0.0 --port=5000 app:app
```

Dashboard : http://localhost:5000

### Sur Windows Server 2019/2022 (recommandé pour l'agent ACME)

1. Installez Python 3 et lancez l'application sur le serveur IIS lui-même.
2. Exécutez-le en service (NSSM, `sc.exe` ou tâche planifiée « au démarrage ») :
   ```
   nssm install CertMonitor "C:\path\to\.venv\Scripts\python.exe" "C:\path\to\cert-monitor\run.py"
   ```
3. Le process doit tourner avec des droits suffisants pour `Import-PfxCertificate` et `WebAdministration` (compte administrateur ou LOCAL SYSTEM).

## Configuration (`.env`)

### Surveillance & e-mail

| Variable | Défaut | Description |
|---|---|---|
| `WARN_DAYS` / `CRITICAL_DAYS` | `30` / `7` | Seuils d'affichage dashboard |
| `NOTIFY_DAYS` | `30` | Seuils d'e-mail (ex. `30,15,7`) |
| `CHECK_INTERVAL_HOURS` | `6` | Fréquence des vérifications |
| `SMTP_*`, `MAIL_FROM`, `MAIL_TO` | — | Serveur et destinataires SMTP |

### ACME (renouvellements)

| Variable | Défaut | Description |
|---|---|---|
| `ACME_DIRECTORY_URL` | production LE | Mettre `https://acme-staging-v02.api.letsencrypt.org/directory` pour tester |
| `ACME_EMAIL` | — | E-mail du compte Let's Encrypt |
| `ACME_CHALLENGE_MODE` | `webroot` | `webroot` (IIS sert le fichier) ou `standalone` (port 80) |
| `ACME_WEBROOT` | — | Racine du site IIS, ex. `C:\inetpub\wwwroot` |
| `ACME_STANDALONE_PORT` | `80` | Port du serveur de défi en mode standalone |
| `ACME_PFX_PASSWORD` | `cert-monitor` | Mot de passe PFX (export + import IIS) |
| `RENEW_BEFORE_DAYS` | `30` | Renouvellement auto quand il reste ≤ N jours |
| `ACME_OUTPUT_DIR` | `data/certs` | Export `.pem` / `.key` / `.pfx` |

## Fonctionnement

### Renouvellement automatique

À chaque cycle du planificateur (toutes les `CHECK_INTERVAL_HOURS`), pour chaque tâche active :

1. Vérification du certificat actuel du domaine (port 443).
2. Si expiration dans ≤ `RENEW_BEFORE_DAYS` jours → commande ACME complète :
   compte → newOrder → défi http-01 (fichier écrit dans le webroot IIS ou servi sur le port 80) → validation → finalisation (CSR) → téléchargement de la chaîne.
3. Installation : import PFX dans le magasin `LocalMachine\My`, binding HTTPS 443 (SNI) sur le site IIS indiqué via `AddSslCertificate`. Sinon export des fichiers `.pem`/`.key`/`.pfx`.
4. Journal détaillé consultable depuis le dashboard (bouton 📜).

Un seul e-mail d'alerte par seuil et par certificat ; le renouvellement réarme naturellement les alertes (nouveau certificat = nouvelle date).

### API

| Méthode | Route | Description |
|---|---|---|
| `GET/POST` | `/api/sites` | Sites surveillés |
| `PUT/DELETE` | `/api/sites/:id` | Modifier / supprimer |
| `POST` | `/api/sites/:id/check` | Vérification manuelle |
| `POST` | `/api/check-all` | Vérifier tous les sites |
| `GET/POST` | `/api/renewals` | Tâches de renouvellement ACME |
| `PUT/DELETE` | `/api/renewals/:id` | Modifier / supprimer une tâche |
| `POST` | `/api/renewals/:id/renew` | Renouvellement immédiat (forcé) |
| `POST` | `/api/renewals/run-due` | Exécuter les tâches arrivées à échéance |
| `GET` | `/api/renewals/:id/log` | Journal d'une exécution |
| `GET` | `/api/config` | Configuration courante |

## Tests

```bash
pip install cryptography
python tests/test_acme.py
```

Le test lance un **serveur ACME simulé** (compte, commande, défi http-01, finalisation, chaîne signée par une CA de test) et vérifie le client de bout en bout : JWS, webroot, CSR, PFX.

## Notes de sécurité

- `data/acme_account.key` = clé du compte Let's Encrypt : **sauvegardez-la**, hors contrôle de version (déjà dans `.gitignore`).
- Le dossier `data/` contient clés privées et PFX : restreignez les droits NTFS.
- Testez d'abord avec le **staging** Let's Encrypt (limites de taux de production).
