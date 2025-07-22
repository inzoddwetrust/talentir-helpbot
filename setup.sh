#!/bin/bash

INSTALL_PATH="/opt/helpbot"
SERVICE_NAME="helpbot"
GITHUB_REPO="git@github.com:inzoddwetrust/helpbot.git"

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

# Прекращаем выполнение скрипта при любой ошибке
set -e

# Проверка и установка необходимых утилит
command -v ssh >/dev/null 2>&1 || {
   log "SSH is required but not installed. Installing..."
   apt-get install -y openssh-client
}

# Обновление системы
log "Updating system..."
apt-get update
apt-get upgrade -y

log "Installing sudo..."
apt-get install -y sudo

# Установка системных зависимостей
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

# Проверка наличия SSH ключа
if [ ! -f ~/.ssh/id_ed25519 ]; then
   log "SSH key not found. Generating..."
   ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519

   echo "Please add this public key to your GitHub repository deploy keys:"
   echo "------------------------"
   cat ~/.ssh/id_ed25519.pub
   echo "------------------------"

   echo "After adding the key to GitHub, press Enter to continue..."
   read -r
fi

# Проверка подключения к GitHub
if ! ssh -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
   log "Error: Cannot authenticate with GitHub"
   log "Please ensure the SSH key is added to GitHub deploy keys"
   exit 1
fi

# Определение пользователя
if [ -n "$SUDO_USER" ]; then
   INSTALL_USER="$SUDO_USER"
else
   INSTALL_USER="$USER"
fi

# Проверка существующей установки
if [ -d "$INSTALL_PATH" ]; then
   log "Found existing installation at $INSTALL_PATH"

   if systemctl list-unit-files | grep -q "$SERVICE_NAME"; then
       log "Stopping existing service..."
       systemctl stop "$SERVICE_NAME"
       systemctl disable "$SERVICE_NAME"
   fi

   if [ -d "$INSTALL_PATH/bot" ]; then
       log "Creating backup of existing bot..."
       backup_dir="$INSTALL_PATH/backup_$(date +'%Y%m%d_%H%M%S')"
       mv "$INSTALL_PATH/bot" "$backup_dir"
       log "Backup created at $backup_dir"
   fi

   if [ -d "$INSTALL_PATH/venv" ]; then
       log "Removing existing virtual environment..."
       rm -rf "$INSTALL_PATH/venv"
   fi
else
   mkdir -p "$INSTALL_PATH"
fi

chown "$INSTALL_USER:$INSTALL_USER" "$INSTALL_PATH"

# Клонирование репозитория
log "Cloning repository..."
su - "$INSTALL_USER" -c "git clone $GITHUB_REPO $INSTALL_PATH/bot" || {
   log "Failed to clone repository"
   exit 1
}

# Создание виртуального окружения
log "Creating virtual environment..."
python3 -m venv "$INSTALL_PATH/venv"
chown -R "$INSTALL_USER:$INSTALL_USER" "$INSTALL_PATH/venv"

# Создание requirements-debian.txt если нужно
log "Preparing requirements for Debian..."
su - "$INSTALL_USER" -c "
   cd $INSTALL_PATH/bot && \
   if [ ! -f requirements-debian.txt ]; then
       # Создаем адаптированную версию requirements.txt для Debian
       cp requirements.txt requirements-debian.txt
       # Убираем macOS-специфичные пакеты если есть
       sed -i '/^pyobjc/d' requirements-debian.txt 2>/dev/null || true
   fi
"

# Активация виртуального окружения и установка пакетов
log "Installing Python dependencies..."
su - "$INSTALL_USER" -c "
   source $INSTALL_PATH/venv/bin/activate && \
   cd $INSTALL_PATH/bot && \
   pip install --upgrade pip && \
   pip install -r requirements-debian.txt
"

# Создание конфигурационных файлов и директорий
log "Creating configuration files and directories..."
su - "$INSTALL_USER" -c "
   cd $INSTALL_PATH/bot && \
   mkdir -p temp logs creds
"

# Создание .env если не существует
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

# Создание systemd сервиса
log "Creating systemd service..."
tee /etc/systemd/system/"$SERVICE_NAME".service << EOF
[Unit]
Description=HelpBot Support System
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

# Перезагрузка systemd
log "Reloading systemd..."
systemctl daemon-reload

# Создание логrotate конфигурации
log "Setting up log rotation..."
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

log "Setup complete! Please:"
log "1. Edit .env file in $INSTALL_PATH/bot/ with your actual credentials"
log "2. Place helpbot_key.json in $INSTALL_PATH/bot/creds/"
log "3. Update MAINBOT_DATABASE_URL in .env to point to correct mainbot database"
log "4. Run these commands when configuration is ready:"
log "   sudo systemctl enable $SERVICE_NAME"
log "   sudo systemctl start $SERVICE_NAME"
log "5. Check status with: sudo systemctl status $SERVICE_NAME"
log "6. View logs with: sudo journalctl -u $SERVICE_NAME -f"