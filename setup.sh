#!/bin/bash

# Автоматическое определение проекта из имени файла
SCRIPT_NAME=$(basename "$0")
if [[ "$SCRIPT_NAME" =~ setup-(.+)\.sh$ ]]; then
    PROJECT_NAME="${BASH_REMATCH[1]}"
else
    echo "ERROR: Script must be named like 'setup-PROJECT-NAME.sh'"
    echo "Example: setup-talentir-helpbot.sh, setup-jetup-helpbot.sh"
    exit 1
fi

# Настройки на основе имени проекта
INSTALL_PATH="/opt/${PROJECT_NAME}"
SERVICE_NAME="${PROJECT_NAME}"
GITHUB_REPO="git@github.com:inzoddwetrust/${PROJECT_NAME}.git"

echo "=========================================="
echo "🚀 Installing ${PROJECT_NAME}"
echo "=========================================="
echo "• Project: ${PROJECT_NAME}"
echo "• Repository: ${GITHUB_REPO}"
echo "• Install path: ${INSTALL_PATH}"
echo "• Service name: ${SERVICE_NAME}"
echo ""

# Функция логирования
log() {
   echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

# Функция обработки ошибок
handle_error() {
   log "Error occurred in script at line: ${1}"
   exit 1
}

trap 'handle_error ${LINENO}' ERR

# Проверка root прав
if [ "$EUID" -ne 0 ]; then
   log "Please run as root (with sudo)"
   exit 1
fi

set -e

# Проверка и установка SSH
command -v ssh >/dev/null 2>&1 || {
   log "Installing SSH client..."
   apt-get update
   apt-get install -y openssh-client
}

# Обновление системы
log "Updating system..."
apt-get update
apt-get upgrade -y

log "Installing sudo..."
apt-get install -y sudo

# Установка зависимостей
log "Installing system dependencies..."
apt-get install -y \
   build-essential \
   python3 \
   python3-venv \
   python3-dev \
   python3-pip \
   git \
   openssh-client \
   sqlite3 \
   libssl-dev \
   zlib1g-dev \
   libbz2-dev \
   libreadline-dev \
   libsqlite3-dev \
   wget \
   curl \
   llvm \
   libncurses5-dev \
   libncursesw5-dev \
   xz-utils \
   tk-dev \
   libffi-dev \
   liblzma-dev \
   pkg-config \
   libcairo2-dev \
   libjpeg-dev \
   libgif-dev

# SSH ключи для конкретного проекта
log "Setting up SSH key for ${PROJECT_NAME}..."

mkdir -p ~/.ssh
chmod 700 ~/.ssh

# Уникальный ключ для этого проекта и установки
UNIQUE_ID="${PROJECT_NAME}_$(date +%Y%m%d_%H%M%S)_$(head /dev/urandom | tr -dc a-z0-9 | head -c 6)"
SSH_KEY="$HOME/.ssh/id_ed25519_${UNIQUE_ID}"
SSH_CONFIG="$HOME/.ssh/config"
SSH_HOST_ALIAS="github.com-${UNIQUE_ID}"

log "Creating unique SSH key: ${UNIQUE_ID}"

# Генерируем уникальный ключ
ssh-keygen -t ed25519 -N "" -f "$SSH_KEY" -C "${PROJECT_NAME}-deploy-${UNIQUE_ID}"
chmod 600 "$SSH_KEY"
chmod 644 "$SSH_KEY.pub"

# Добавляем GitHub в known_hosts
if ! grep -q "github.com" ~/.ssh/known_hosts 2>/dev/null; then
    log "Adding GitHub to known hosts..."
    ssh-keyscan -H github.com >> ~/.ssh/known_hosts 2>/dev/null
fi

# SSH config
cat >> "$SSH_CONFIG" << EOF

# ${PROJECT_NAME} installation ${UNIQUE_ID} - $(date)
Host ${SSH_HOST_ALIAS}
    HostName github.com
    User git
    IdentityFile $SSH_KEY
    IdentitiesOnly yes
EOF

chmod 600 "$SSH_CONFIG"

# URL с уникальным хостом
REPO_URL="git@${SSH_HOST_ALIAS}:inzoddwetrust/${PROJECT_NAME}.git"

echo ""
echo "=========================================="
echo "SSH KEY FOR ${PROJECT_NAME}"
echo "=========================================="
echo ""
echo "Generated unique key for: ${PROJECT_NAME}"
echo "Key ID: ${UNIQUE_ID}"
echo ""
echo "Add this key to GitHub repository:"
echo ""
echo "1. Copy this public key:"
echo "----------------------------------------"
cat "$SSH_KEY.pub"
echo "----------------------------------------"
echo ""
echo "2. Go to: https://github.com/inzoddwetrust/${PROJECT_NAME}/settings/keys"
echo "3. Click 'Add deploy key'"
echo "4. Title: '${PROJECT_NAME} Server - ${UNIQUE_ID}'"
echo "5. Paste the key above"
echo "6. Leave 'Allow write access' UNCHECKED"
echo "7. Click 'Add key'"
echo ""
echo "IMPORTANT: Add to ${PROJECT_NAME} repository, not any other!"
echo ""
echo "Press Enter after adding the key..."
read -r

# Проверка доступа к конкретному репозиторию
log "Verifying access to ${PROJECT_NAME} repository..."
sleep 2

if timeout 15 git ls-remote "$REPO_URL" HEAD >/dev/null 2>&1; then
    log "SUCCESS: ${PROJECT_NAME} repository access confirmed!"
else
    echo ""
    echo "❌ ERROR: Cannot access ${PROJECT_NAME} repository"
    echo ""
    echo "Make sure you added the key to the correct repository:"
    echo "https://github.com/inzoddwetrust/${PROJECT_NAME}/settings/keys"
    echo ""
    echo "Test manually: git ls-remote $REPO_URL HEAD"
    exit 1
fi

# Определение пользователя
if [ -n "$SUDO_USER" ]; then
   INSTALL_USER="$SUDO_USER"
else
   INSTALL_USER="$USER"
fi

# Управление существующей установкой
if [ -d "$INSTALL_PATH" ]; then
   log "Found existing ${PROJECT_NAME} installation"

   if systemctl list-unit-files | grep -q "$SERVICE_NAME"; then
       log "Stopping existing service..."
       systemctl stop "$SERVICE_NAME" 2>/dev/null || true
       systemctl disable "$SERVICE_NAME" 2>/dev/null || true
   fi

   if [ -d "$INSTALL_PATH/bot" ]; then
       log "Creating backup..."
       backup_dir="$INSTALL_PATH/backup_$(date +'%Y%m%d_%H%M%S')"
       mv "$INSTALL_PATH/bot" "$backup_dir"
       log "Backup: $backup_dir"
   fi

   if [ -d "$INSTALL_PATH/venv" ]; then
       rm -rf "$INSTALL_PATH/venv"
   fi
else
   mkdir -p "$INSTALL_PATH"
fi

chown "$INSTALL_USER:$INSTALL_USER" "$INSTALL_PATH"

# Клонирование
log "Cloning ${PROJECT_NAME} repository..."
su - "$INSTALL_USER" -c "git clone $REPO_URL $INSTALL_PATH/bot" || {
   log "Failed to clone ${PROJECT_NAME} repository"
   log "Verify the SSH key was added to: https://github.com/inzoddwetrust/${PROJECT_NAME}/settings/keys"
   exit 1
}

log "${PROJECT_NAME} repository cloned successfully"

# Python окружение
log "Creating Python virtual environment..."
python3 -m venv "$INSTALL_PATH/venv"
chown -R "$INSTALL_USER:$INSTALL_USER" "$INSTALL_PATH/venv"

# Requirements оптимизированные
log "Creating optimized requirements..."
su - "$INSTALL_USER" -c "
   cd $INSTALL_PATH/bot && \
   cat > requirements-debian.txt << 'EOF'
aiofiles==24.1.0
aiogram==3.19.0
aiohappyeyeballs==2.6.1
aiohttp==3.11.16
aiosignal==1.3.2
annotated-types==0.7.0
async-timeout==5.0.1
attrs==25.3.0
cachetools==5.5.2
certifi==2025.1.31
charset-normalizer==3.4.1
frozenlist==1.5.0
google-api-core==2.24.2
google-api-python-client==2.167.0
google-auth==2.39.0
google-auth-httplib2==0.2.0
google-auth-oauthlib==1.2.1
googleapis-common-protos==1.70.0
greenlet==3.1.1
gspread==6.2.0
httplib2==0.22.0
idna==3.10
magic-filter==1.0.12
multidict==6.4.2
oauthlib==3.2.2
propcache==0.3.1
proto-plus==1.26.1
protobuf==6.30.2
pyasn1==0.6.1
pyasn1-modules==0.4.2
pyparsing==3.2.3
python-dotenv==1.1.0
requests==2.32.3
requests-oauthlib==2.0.0
rsa==4.9
SQLAlchemy==2.0.40
typing-extensions==4.13.1
uritemplate==4.1.1
urllib3==2.4.0
yarl==1.19.0
EOF
"

# Установка пакетов
log "Installing Python dependencies..."
su - "$INSTALL_USER" -c "
   source $INSTALL_PATH/venv/bin/activate && \
   cd $INSTALL_PATH/bot && \
   pip install --upgrade pip && \
   pip install -r requirements-debian.txt
"

# Директории
log "Creating directories..."
su - "$INSTALL_USER" -c "
   cd $INSTALL_PATH/bot && \
   mkdir -p temp logs creds
"

# .env шаблон
if [ ! -f "$INSTALL_PATH/bot/.env" ]; then
   log "Creating .env template..."
   su - "$INSTALL_USER" -c "
   cd $INSTALL_PATH/bot && \
   cat > .env << 'EOF'
# Bot credentials
API_TOKEN=YOUR_BOT_TOKEN_HERE
ADMINS=YOUR_ADMIN_IDS_HERE

# Database URLs
HELPBOT_DATABASE_URL=sqlite:///helpbot.db
MAINBOT_DATABASE_URL=sqlite:///mainbot.db

# Google Sheets
GOOGLE_SHEET_ID=YOUR_GOOGLE_SHEET_ID_HERE
GOOGLE_CREDENTIALS_JSON=creds/helpbot_key.json

# Helpbot specific
HELPBOT_GROUP_ID=YOUR_GROUP_ID_HERE
EOF
   "
fi

# Systemd сервис
log "Creating systemd service..."
tee /etc/systemd/system/"$SERVICE_NAME".service << EOF
[Unit]
Description=${PROJECT_NAME} Support System
After=network.target

[Service]
Type=simple
User=$INSTALL_USER
WorkingDirectory=$INSTALL_PATH/bot
Environment="PATH=$INSTALL_PATH/venv/bin"
ExecStart=$INSTALL_PATH/venv/bin/python3 helpbot.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# Logrotate
tee /etc/logrotate.d/"$SERVICE_NAME" << EOF
$INSTALL_PATH/bot/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 644 $INSTALL_USER $INSTALL_USER
    postrotate
        systemctl reload-or-restart $SERVICE_NAME > /dev/null 2>&1 || true
    endscript
}
EOF

echo ""
echo "=========================================="
echo "✅ ${PROJECT_NAME} INSTALLATION COMPLETE!"
echo "=========================================="
echo ""
echo "Project: ${PROJECT_NAME}"
echo "SSH Key: ${UNIQUE_ID}"
echo "Path: ${INSTALL_PATH}"
echo "Service: ${SERVICE_NAME}"
echo ""
echo "Next steps:"
echo "1. Edit: nano $INSTALL_PATH/bot/.env"
echo "2. Add Google creds to: $INSTALL_PATH/bot/creds/"
echo "3. Start: sudo systemctl enable $SERVICE_NAME && sudo systemctl start $SERVICE_NAME"
echo "4. Monitor: sudo journalctl -u $SERVICE_NAME -f"
echo ""
echo "🎉 ${PROJECT_NAME} ready to configure!"