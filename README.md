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

### Sur Windows Server 2019/2022 (recommandé pour l'agent ACME) — script all-in-one

Copiez le dossier du projet sur le serveur puis, dans une console PowerShell **administrateur** :

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\install.ps1 \
  -SmtpHost smtp.exemple.com -SmtpPort 587 \
  -SmtpUser alertes@exemple.com -SmtpPassword "motdepasse" \
  -MailFrom alertes@exemple.com -MailTo "admin@exemple.com,dsi@exemple.com" \
  -AcmeEmail admin@exemple.com \
  -AcmeWebroot "C:\inetpub\wwwroot" \
  -AcmeChallengeMode webroot \
  -AcmeStaging
```

Le script effectue tout : vérification des prérequis (OS, IIS), installation silencieuse de Python 3.12 si absent, environnement virtuel + dépendances, **génération du `.env` à partir des arguments**, règles de pare-feu, service Windows (NSSM téléchargé automatiquement, repli sur tâche planifiée SYSTEM au démarrage), démarrage et test de santé.

Paramètres principaux :

| Paramètre | Défaut | Rôle |
|---|---|---|
| `-InstallDir` | `C:\cert-monitor` | Dossier d'installation |
| `-Port` | `5000` | Port du dashboard |
| `-ServiceName` | `CertMonitor` | Nom du service Windows |
| `-SmtpHost/-SmtpPort/-SmtpUser/-SmtpPassword` | — | Serveur SMTP |
| `-MailFrom/-MailTo` | — | Expéditeur / destinataires (virgules) |
| `-AcmeEmail` | — | Compte Let's Encrypt |
| `-AcmeChallengeMode` | `webroot` | `webroot` ou `standalone` |
| `-AcmeWebroot` | `C:\inetpub\wwwroot` | Racine du site IIS pour les défis |
| `-AcmePfxPassword` | aléatoire | Mot de passe PFX (généré si omis) |
| `-RenewBeforeDays` | `30` | Renouvellement auto à J-N |
| `-AcmeStaging` | off | Utilise le staging Let's Encrypt (tests) |
| `-Force` | off | Écrase le `.env` existant |
| `-Uninstall` | — | Supprime le service / la tâche planifiée |

Exemples :

```powershell
# Réinstallation avec nouveau .env
.\install.ps1 -Force -SmtpHost smtp.exemple.com -MailTo admin@exemple.com -AcmeEmail admin@exemple.com

# Désinstallation
.\install.ps1 -Uninstall
```

Le process doit tourner avec des droits suffisants pour `Import-PfxCertificate` et `WebAdministration` (le service est installé en LOCAL SYSTEM via NSSM).

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

### Sur Debian 13 (trixie) — dashboard uniquement

```bash
sudo ./install-debian.sh \
  --smtp-host smtp.exemple.com --smtp-port 587 \
  --smtp-user alertes@exemple.com --smtp-password "xxx" \
  --mail-from alertes@exemple.com --mail-to "admin@exemple.com" \
  --acme-email admin@exemple.com \
  --acme-staging
```

Le script est **one-shot** : paquets système, utilisateur dédié `certmonitor`, venv, `.env` généré depuis les arguments, pare-feu, **service systemd** durci (redémarrage auto) et test de santé.

**Écoute réseau** : par défaut le dashboard écoute sur `0.0.0.0:5000` (accessible depuis le réseau local, port ouvert dans le pare-feu si ufw/firewalld actifs). Derrière un vhost **nginx** déjà configuré, ajoutez `--bind 127.0.0.1` et utilisez dans le vhost :

```nginx
proxy_pass http://127.0.0.1:5000;
```

L'adresse d'écoute est modifiable après coup dans le `.env` (`BIND_HOST`, `BIND_PORT`) puis `sudo systemctl restart cert-monitor`.

Options : `--uninstall`, `--force`, `--port`, `--bind`, `--install-dir`, `--user`, `--service-name`, `--acme-staging`.

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
