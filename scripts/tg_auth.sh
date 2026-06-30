#!/bin/bash
# Скрипт для авторизации Telethon-парсера.
# Запусти ОДИН РАЗ, потом session.session сохранится и парсер будет работать без ввода.

set -e
cd "$(dirname "$0")/.."

# Находим venv
if [ -d ".venv" ]; then
    VENV_DIR=".venv"
elif [ -d "venv" ]; then
    VENV_DIR="venv"
else
    VENV_DIR=""
fi

# Активируем venv если есть
if [ -n "$VENV_DIR" ] && [ -f "$VENV_DIR/bin/activate" ]; then
    echo "✅ Активирую venv: $VENV_DIR"
    # shellcheck disable=SC1091
    source "$VENV_DIR/bin/activate"
    PYTHON="python"
else
    echo "⚠ venv не найден, использую системный python"
    PYTHON="python3"
fi

# Загружаем .env если не загружен
if [ -z "$TG_API_ID" ] || [ -z "$TG_API_HASH" ]; then
    if [ -f bot/.env ]; then
        echo "✅ Загружаю bot/.env"
        set -a
        # shellcheck disable=SC1091
        source bot/.env
        set +a
    fi
fi

if [ -z "$TG_API_ID" ] || [ -z "$TG_API_HASH" ]; then
    echo "❌ TG_API_ID / TG_API_HASH не заданы"
    echo "Добавь их в bot/.env:"
    echo "  TG_API_ID=..."
    echo "  TG_API_HASH=..."
    exit 1
fi

# Удаляем старую session если есть (чтобы начать заново)
if [ -f scripts/session.session ]; then
    echo "⚠  Найдена старая session — удаляю..."
    rm -f scripts/session.session
fi

echo "==================================="
echo "Telethon авторизация"
echo "==================================="
echo "Python: $($PYTHON -V 2>&1)"
echo "Telethon: $($PYTHON -c 'import telethon; print(telethon.__version__)' 2>&1)"
echo ""
echo "⚠️  ВАЖНО: НУЖЕН НОМЕР ТЕЛЕФОНА, а НЕ бот-токен!"
echo "⚠️  Бот НЕ МОЖЕТ подписываться на каналы — нужна ТВОЯ личная Telegram-аккаунт!"
echo ""
echo "Например: +79161234567"
echo ""
echo "⚠️  НЕ вводи бот-токен вида 8626418506:AAGh..."
echo ""
echo "После этого придёт SMS-код в Telegram"
echo "Session сохранится в scripts/session.session"
echo ""

$PYTHON scripts/parse_tg_channels.py
