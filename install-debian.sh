#!/usr/bin/env bash
# ============================================================================
# Cert Monitor v2 — Installation du dashboard sur Debian 13 (trixie)
#
# Usage :
#   sudo ./install-debian.sh \
#     --smtp-host smtp.exemple.com --smtp-port 587 \
#     --smtp-user alertes@exemple.com --smtp-password "xxx" \
#     --mail-from alertes@exemple.com --mail-to "admin@exemple.com" \
#     --acme-email admin@exemple.com --acme-staging
#
# Écoute réseau : par défaut le dashboard écoute sur 0.0.0.0 (réseau local).
# Derrière un vhost nginx local, préférez : --bind 127.0.0.1 puis dans le vhost :
#     proxy_pass http://127.0.0.1:5000;
#
#   sudo ./install-debian.sh --uninstall
# ============================================================================
set -euo pipefail
trap 'c_err "Échec de la commande à la ligne $LINENO (voir le message ci-dessus)."' ERR

# --- Défauts -----------------------------------------------------------------
INSTALL_DIR="/opt/cert-monitor"
APP_USER="certmonitor"
SERVICE_NAME="cert-monitor"
PORT=5000

SMTP_HOST=""
SMTP_PORT=587
SMTP_USER=""
SMTP_PASSWORD=""
MAIL_FROM=""
MAIL_TO=""

ACME_EMAIL=""
ACME_DIRECTORY_URL="https://acme-v02.api.letsencrypt.org/directory"
ACME_STAGING=0
ACME_CHALLENGE_MODE="webroot"
ACME_WEBROOT=""
ACME_STANDALONE_PORT=80
ACME_PFX_PASSWORD=""
RENEW_BEFORE_DAYS=30

WARN_DAYS=30
CRITICAL_DAYS=7
NOTIFY_DAYS="30"
CHECK_INTERVAL_HOURS=6

FORCE=0
UNINSTALL=0

# --- Couleurs ----------------------------------------------------------------
c_info()  { printf '\033[36m==> %s\033[0m\n' "$*"; }
c_ok()    { printf '\033[32m    OK : %s\033[0m\n' "$*"; }
c_warn()  { printf '\033[33m    ATTENTION : %s\033[0m\n' "$*"; }
c_err()   { printf '\033[31mERREUR : %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    sed -n '2,20p' "$0"
    exit 0
}

# --- Arguments ---------------------------------------------------------------
while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir)          INSTALL_DIR="$2"; shift 2 ;;
        --user)                 APP_USER="$2"; shift 2 ;;
        --service-name)         SERVICE_NAME="$2"; shift 2 ;;
        --port)                 PORT="$2"; shift 2 ;;
        --bind)                 BIND_HOST="$2"; shift 2 ;;
        --smtp-host)            SMTP_HOST="$2"; shift 2 ;;
        --smtp-port)            SMTP_PORT="$2"; shift 2 ;;
        --smtp-user)            SMTP_USER="$2"; shift 2 ;;
        --smtp-password)        SMTP_PASSWORD="$2"; shift 2 ;;
        --mail-from)            MAIL_FROM="$2"; shift 2 ;;
        --mail-to)              MAIL_TO="$2"; shift 2 ;;
        --acme-email)           ACME_EMAIL="$2"; shift 2 ;;
        --acme-directory-url)   ACME_DIRECTORY_URL="$2"; shift 2 ;;
        --acme-staging)         ACME_STAGING=1; shift ;;
        --acme-challenge-mode)  ACME_CHALLENGE_MODE="$2"; shift 2 ;;
        --acme-webroot)         ACME_WEBROOT="$2"; shift 2 ;;
        --acme-standalone-port) ACME_STANDALONE_PORT="$2"; shift 2 ;;
        --renew-before-days)    RENEW_BEFORE_DAYS="$2"; shift 2 ;;
        --notify-days)          NOTIFY_DAYS="$2"; shift 2 ;;
        --check-interval-hours) CHECK_INTERVAL_HOURS="$2"; shift 2 ;;
        --force)                FORCE=1; shift ;;
        --uninstall)            UNINSTALL=1; shift ;;
        -h|--help)              usage ;;
        *) c_err "Argument inconnu : $1 (voir --help)" ;;
    esac
done

# --- Désinstallation ---------------------------------------------------------
if [ "$UNINSTALL" -eq 1 ]; then
    c_info "Désinstallation de $SERVICE_NAME"
    systemctl stop "$SERVICE_NAME" 2>/dev/null || true
    systemctl disable "$SERVICE_NAME" 2>/dev/null || true
    rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
    systemctl daemon-reload
    c_ok "Service $SERVICE_NAME supprimé"
    echo ""
    echo "Les fichiers restent dans $INSTALL_DIR (supprimez-les manuellement si besoin)."
    exit 0
fi

# --- Prérequis ---------------------------------------------------------------
[ "$(id -u)" -eq 0 ] || c_err "À exécuter en root (sudo)."

c_info "Vérification des prérequis"
if [ -r /etc/os-release ]; then
    . /etc/os-release
    if [ "${VERSION_ID:-}" = "13" ] || [ "${VERSION_CODENAME:-}" = "trixie" ]; then
        c_ok "Debian 13 ($PRETTY_NAME) détectée"
    else
        c_warn "Debian 13 non détectée (trouvé : ${PRETTY_NAME:-inconnu}), installation poursuivie quand même."
    fi
else
    c_warn "/etc/os-release introuvable : distribution non vérifiée."
fi

BIND_HOST="0.0.0.0"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[ -f "$SCRIPT_DIR/app.py" ] || c_err "Lancez ce script depuis le dossier du projet (app.py introuvable à côté de install-debian.sh)."

c_info "Installation des paquets système (python3, venv, curl)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv curl ca-certificates >/dev/null
PY_VERSION="$(python3 --version)"
c_ok "$PY_VERSION installé"

# --- Copie des fichiers ------------------------------------------------------
c_info "Copie des fichiers vers $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" "$INSTALL_DIR/data" "$INSTALL_DIR/logs"
cp -r "$SCRIPT_DIR/app.py" "$SCRIPT_DIR/run.py" "$SCRIPT_DIR/requirements.txt" \
      "$SCRIPT_DIR/monitor" "$SCRIPT_DIR/templates" "$INSTALL_DIR/"
find "$INSTALL_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

if ! id "$APP_USER" >/dev/null 2>&1; then
    useradd --system --home-dir "$INSTALL_DIR" --shell /usr/sbin/nologin "$APP_USER"
    c_ok "Utilisateur système $APP_USER créé"
else
    c_ok "Utilisateur $APP_USER déjà présent"
fi

# --- Environnement virtuel ---------------------------------------------------
c_info "Création de l'environnement virtuel et installation des dépendances"
python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install --quiet -r "$INSTALL_DIR/requirements.txt"
c_ok "Dépendances installées (flask, waitress, cryptography)"

# --- Fichier .env ------------------------------------------------------------
c_info "Génération du fichier .env"
ENV_FILE="$INSTALL_DIR/.env"
if [ -f "$ENV_FILE" ] && [ "$FORCE" -ne 1 ]; then
    c_warn ".env existe déjà, conservé (utilisez --force pour l'écraser)."
else
    if [ -z "$ACME_PFX_PASSWORD" ]; then
        PW="$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')"
        ACME_PFX_PASSWORD="${PW:0:24}"
        echo "    Mot de passe PFX généré aléatoirement."
    fi
    if [ "$ACME_STAGING" -eq 1 ]; then
        ACME_DIRECTORY_URL="https://acme-staging-v02.api.letsencrypt.org/directory"
    fi
    cat > "$ENV_FILE" <<EOF
DB_PATH=data/monitor.db

# --- Surveillance ---
WARN_DAYS=$WARN_DAYS
CRITICAL_DAYS=$CRITICAL_DAYS
NOTIFY_DAYS=$NOTIFY_DAYS
CHECK_INTERVAL_HOURS=$CHECK_INTERVAL_HOURS

# --- E-mail (SMTP) ---
SMTP_HOST=$SMTP_HOST
SMTP_PORT=$SMTP_PORT
SMTP_USER=$SMTP_USER
SMTP_PASSWORD=$SMTP_PASSWORD
SMTP_USE_TLS=true
MAIL_FROM=$MAIL_FROM
MAIL_TO=$MAIL_TO

# --- ACME / Let's Encrypt ---
ACME_DIRECTORY_URL=$ACME_DIRECTORY_URL
ACME_EMAIL=$ACME_EMAIL
ACME_ACCOUNT_KEY_PATH=data/acme_account.key
ACME_OUTPUT_DIR=data/certs
ACME_CHALLENGE_MODE=$ACME_CHALLENGE_MODE
ACME_WEBROOT=$ACME_WEBROOT
ACME_STANDALONE_PORT=$ACME_STANDALONE_PORT
ACME_PFX_PASSWORD=$ACME_PFX_PASSWORD
RENEW_BEFORE_DAYS=$RENEW_BEFORE_DAYS

# --- Écoute réseau ---
# 127.0.0.1 = accès via le vhost nginx (recommandé) ; 0.0.0.0 = accès direct réseau local
BIND_HOST=$BIND_HOST
BIND_PORT=$PORT

BASE_URL=http://localhost:$PORT
EOF
    c_ok ".env généré : $ENV_FILE"
fi
chown -R "$APP_USER:$APP_USER" "$INSTALL_DIR"
chmod 600 "$ENV_FILE" 2>/dev/null || true
chmod 700 "$INSTALL_DIR/data" 2>/dev/null || true

# --- Pare-feu ----------------------------------------------------------------
c_info "Pare-feu"
if [ "$BIND_HOST" = "127.0.0.1" ]; then
    c_ok "Écoute sur 127.0.0.1:$PORT uniquement : pas d'ouverture de port (accès via le vhost nginx)."
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$PORT/tcp" >/dev/null
    c_ok "Port $PORT ouvert via ufw"
elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port="${PORT}/tcp" >/dev/null
    firewall-cmd --reload >/dev/null
    c_ok "Port $PORT ouvert via firewalld"
else
    c_warn "Aucun pare-feu actif détecté (ufw/firewalld). Vérifiez vos règles nftables si nécessaire."
fi

# --- Service systemd ---------------------------------------------------------
c_info "Installation du service systemd '$SERVICE_NAME'"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=Cert Monitor v2 — surveillance des certificats HTTPS
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$INSTALL_DIR
ExecStart=$INSTALL_DIR/.venv/bin/python run.py
Restart=always
RestartSec=5
StandardOutput=append:$INSTALL_DIR/logs/service-out.log
StandardError=append:$INSTALL_DIR/logs/service-err.log

# Durcissement
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=$INSTALL_DIR

[Install]
WantedBy=multi-user.target
EOF
systemctl daemon-reload
systemctl enable "$SERVICE_NAME" >/dev/null 2>&1 || true
if systemctl restart "$SERVICE_NAME"; then
    c_ok "Service installé et démarré ($(systemctl is-active "$SERVICE_NAME"))"
else
    c_warn "Le service n'a pas démarré : journalctl -u $SERVICE_NAME -e"
fi

# --- Test de santé -----------------------------------------------------------
c_info "Test de santé"
HEALTHY=0
for _ in $(seq 1 15); do
    sleep 2
    if curl -fsS -o /dev/null "http://localhost:${PORT}/api/config"; then
        HEALTHY=1
        break
    fi
done
if [ "$HEALTHY" -eq 1 ]; then
    c_ok "Application en ligne sur http://localhost:$PORT"
else
    c_warn "L'application ne répond pas encore. Consultez : journalctl -u $SERVICE_NAME -e"
fi

echo ""
echo "================= INSTALLATION TERMINÉE ================="
echo " Dashboard      : http://$BIND_HOST:$PORT (vhost nginx : proxy_pass http://$BIND_HOST:$PORT)"
echo " Dossier        : $INSTALL_DIR"
echo " Configuration  : $ENV_FILE"
echo " Logs           : $INSTALL_DIR/logs/ et journalctl -u $SERVICE_NAME"
echo ""
echo " Pensez à :"
echo "  - renseigner SMTP_HOST / MAIL_TO si ce n'est pas fait, puis :"
echo "      sudo systemctl restart $SERVICE_NAME"
if [ "$ACME_STAGING" -eq 0 ]; then
    echo "  - tester d'abord avec --acme-staging (limites de taux Let's Encrypt production)"
fi
echo "=========================================================="
