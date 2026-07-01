#!/bin/bash
# =============================================================
# Бензин рядом — setup.sh
# Первичная настройка VPS (REG.RU Cloud / любая Ubuntu/Debian)
# Запуск от root: sudo bash setup.sh
# =============================================================
set -euo pipefail

APP_USER="benzin"
APP_DIR="/opt/benzin-ryadom"
REPO="https://github.com/DarkTheme404/benzin-ryadom.git"
PYTHON_VER="3.12"

echo "========================================"
echo " Бензин рядом — VPS Setup"
echo "========================================"

# 1. Создаём пользователя
if ! id "$APP_USER" &>/dev/null; then
    echo ">>> Создаю пользователя $APP_USER..."
    useradd -m -s /bin/bash "$APP_USER"
    usermod -aG sudo "$APP_USER"
    echo "$APP_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/$APP_USER
fi

# 2. Устанавливаем зависимости
echo ">>> Устанавливаю пакеты..."
apt-get update -qq
apt-get install -y -qq git python${PYTHON_VER} python${PYTHON_VER}-venv python${PYTHON_VER}-pip \
    build-essential libssl-dev libffi-dev curl wget

# Симлинк на python3.12
if ! command -v python3.12 &>/dev/null; then
    ln -sf /usr/bin/python${PYTHON_VER} /usr/local/bin/python3.12
fi

# 3. Swap (важно при 1 GB RAM)
if [ ! -f /swapfile ]; then
    echo ">>> Создаю swap 1GB..."
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "  swapon: done"
fi

# 3. Клонируем репозиторий
echo ">>> Клонирую репозиторий..."
if [ ! -d "$APP_DIR" ]; then
    git clone "$REPO" "$APP_DIR"
fi
chown -R $APP_USER:$APP_USER "$APP_DIR"

# 4. Создаём venv и устанавливаем зависимости
echo ">>> Устанавливаю Python зависимости..."
sudo -u $APP_USER bash -c "
    cd $APP_DIR
    python3.12 -m venv .venv
    .venv/bin/pip install --upgrade pip
    .venv/bin/pip install -r requirements.txt
"

# 5. Создаём .env из шаблона
if [ ! -f "$APP_DIR/bot/.env" ]; then
    echo ">>> Создаю .env из шаблона..."
    cp "$APP_DIR/deploy/oracle/env.template" "$APP_DIR/bot/.env"
    chown $APP_USER:$APP_USER "$APP_DIR/bot/.env"
    chmod 600 "$APP_DIR/bot/.env"
    echo "!!! ОТРЕДАКТИРУЙ $APP_DIR/bot/.env !!!"
fi

# 6. Устанавливаем systemd сервисы
echo ">>> Устанавливаю systemd сервисы..."
cp "$APP_DIR/deploy/oracle/benzin-bot.service" /etc/systemd/system/
cp "$APP_DIR/deploy/oracle/benzin-parsers.service" /etc/systemd/system/
cp "$APP_DIR/deploy/oracle/benzin-parsers.timer" /etc/systemd/system/

systemctl daemon-reload
systemctl enable benzin-bot.service
systemctl enable benzin-parsers.timer

# 7. Лимит логов (10MB на сервис, на 10GB диск критично)
mkdir -p /etc/systemd/system/benzin-bot.service.d
cat > /etc/systemd/system/benzin-bot.service.d/override.conf << EOF
[Service]
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=60
LogRateLimitBurst=20
EOF

mkdir -p /etc/systemd/system/benzin-parsers.service.d
cat > /etc/systemd/system/benzin-parsers.service.d/override.conf << EOF
[Service]
StandardOutput=journal
StandardError=journal
LogRateLimitIntervalSec=60
LogRateLimitBurst=20
EOF

systemctl daemon-reload

echo "========================================"
echo " Готово!"
echo "========================================"
echo ""
echo "1. Отредактируй .env:"
echo "   sudo nano $APP_DIR/bot/.env"
echo ""
echo "2. Запусти бота:"
echo "   sudo systemctl start benzin-bot"
echo ""
echo "3. Проверь статус:"
echo "   sudo systemctl status benzin-bot"
echo "   sudo journalctl -u benzin-bot -f"
echo ""
echo "4. Парсеры запускаются автоматически каждые 4 часа"
echo "   sudo systemctl start benzin-parsers  # ручной запуск"
echo ""
echo "5. Логи:"
echo "   sudo journalctl -u benzin-bot -f"
echo "   sudo journalctl -u benzin-parsers -f"
echo ""
echo "6. Swap:"
echo "   free -h  # проверить swap"
