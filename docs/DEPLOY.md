# Deployment: парсеры 24/7 на бесплатном VPS

Этот документ — инструкция по развёртыванию парсеров (Telegram, VK, MAX) на бесплатном VPS для непрерывной работы 24/7.

## Зачем нужен VPS

Парсеры требуют постоянного процесса:
- **Telethon (Telegram)** — держит открытое соединение с Telegram, ловит новые сообщения
- **VK API** — сам по себе stateless, но удобно запускать по cron
- **MAX Bot API** — webhook требует публичный URL

На Render Free или Vercel serverless — НЕ работает (нет persistent state, засыпают после 15 мин).
На Mac разработчика — работает, но Mac должен быть включён 24/7.

**Лучший бесплатный вариант: Oracle Cloud Free Tier** — даёт 2 VPS (4 CPU, 24GB RAM) **навсегда бесплатно**.

## Бесплатные варианты

| Сервис | Free Tier | Подходит для нас |
|--------|-----------|------------------|
| **Oracle Cloud Free Tier** | 2 VM (4 CPU, 24GB RAM) навсегда | ✅ лучший вариант |
| **Fly.io** | 3 VM shared-cpu-1x, 256MB RAM, 3GB storage | ✅ подходит |
| **Google Cloud Free** | 1 e2-micro навсегда | ✅ подходит |
| **AWS Free Tier** | 1 t2.micro на 12 месяцев | ⚠ временно |
| **Render** | Background Worker (cron, 1 job) | ⚠ для cron только |
| **Railway** | $5 trial | ❌ trial заканчивается |
| **Vercel** | serverless | ❌ не подходит |

## Oracle Cloud Free Tier — пошаговая инструкция

### 1. Регистрация

1. Перейди на https://cloud.oracle.com/
2. **Start for Free** → укажи email
3. **Важно**: выбери регион **близкий к тебе** (для минимального latency)
   - Европа: Frankfurt, Amsterdam
   - Россия: ❌ нет (ближайшие — Frankfurt, Amsterdam, Stockholm)
4. Введи данные карты. **Oracle списывает и сразу возвращает ~$1** для верификации. Карта не нужна для оплаты — Free Tier навсегда.
5. После регистрации зайди в Console: https://cloud.oracle.com/

### 2. Создание VM

1. **Compute → Instances → Create Instance**
2. **Image**: Oracle Linux 8 (или Ubuntu 22.04)
3. **Shape**: VM.Standard.E2.1.Micro (Always Free-eligible) — 1 CPU, 1GB RAM
   - Или Ampere A1 (4 CPU, 24GB RAM) — но сложнее настройка
4. **Networking**: создай VCN (default подойдёт)
5. **SSH Key**: скачай свой public key (~/.ssh/id_rsa.pub) или создай новый
6. **Boot Volume**: 50GB (бесплатно)
7. Нажми **Create**

### 3. Подключение к VM

```bash
ssh -i ~/.ssh/id_rsa oracle@<PUBLIC_IP>
```

Или через web-консоль Oracle (Instance → Console connection).

### 4. Настройка VM

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y   # если Ubuntu
# или:
sudo dnf update -y                         # если Oracle Linux

# Установка Python 3.12 + pip
sudo dnf install python3.12 python3.12-pip git -y  # Oracle Linux
# или:
sudo apt install python3.12 python3-pip git -y      # Ubuntu

# Создание пользователя (не root)
sudo useradd -m -s /bin/bash benzin
sudo usermod -aG wheel benzin  # для sudo (если Oracle Linux)
sudo -u benzin -i
```

### 5. Деплой кода

```bash
# Клонируем репо
git clone https://github.com/your-username/benzin-ryadom.git
cd benzin-ryadom

# Создаём venv
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt  # или pip install aiogram aiohttp aiosqlite telethon python-dotenv
```

### 6. Создание systemd-сервисов

`/etc/systemd/system/benzin-tg-watcher.service`:
```ini
[Unit]
Description=Benzin TG Watcher (Telethon)
After=network.target

[Service]
Type=simple
User=benzin
WorkingDirectory=/home/benzin/benzin-ryadom
Environment="PATH=/home/benzin/benzin-ryadom/venv/bin"
EnvironmentFile=/home/benzin/benzin-ryadom/bot/.env
ExecStart=/home/benzin/benzin-ryadom/venv/bin/python scripts/parse_tg_channels.py --watch
Restart=always
RestartSec=30
StandardOutput=append:/var/log/benzin-tg.log
StandardError=append:/var/log/benzin-tg.log

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/benzin-vk-parser.timer`:
```ini
[Unit]
Description=Benzin VK Parser (every 30 minutes)
Requires=benzin-vk-parser.service

[Timer]
OnBootSec=5min
OnUnitActiveSec=30min
Persistent=true

[Install]
WantedBy=timers.target
```

`/etc/systemd/system/benzin-vk-parser.service`:
```ini
[Unit]
Description=Benzin VK Parser (one-shot)

[Service]
Type=oneshot
User=benzin
WorkingDirectory=/home/benzin/benzin-ryadom
Environment="PATH=/home/benzin/benzin-ryadom/venv/bin"
EnvironmentFile=/home/benzin/benzin-ryadom/bot/.env
ExecStart=/home/benzin/benzin-ryadom/venv/bin/python scripts/parse_vk.py --query "АИ-95" --limit 50
StandardOutput=append:/var/log/benzin-vk.log
StandardError=append:/var/log/benzin-vk.log
```

```bash
# Активация
sudo systemctl daemon-reload
sudo systemctl enable --now benzin-tg-watcher
sudo systemctl enable --now benzin-vk-parser.timer
sudo systemctl enable --now benzin-vk-parser.service  # первый запуск сразу

# Проверка статуса
sudo systemctl status benzin-tg-watcher
sudo systemctl status benzin-vk-parser.timer
sudo journalctl -u benzin-tg-watcher -f
```

## Настройка credentials на VPS

### Telegram (Telethon)

```bash
# Первый запуск — интерактивная авторизация
cd ~/benzin-ryadom
source venv/bin/activate
python scripts/parse_tg_channels.py --channel benzin_price_ru

# Введи телефон (+79...) и SMS-код
# Создастся scripts/session.session — НЕ УДАЛЯТЬ!
```

`bot/.env`:
```
TG_API_ID=12345678
TG_API_HASH=abcdef0123456789abcdef0123456789
```

### VK API

1. Зайди на https://dev.vk.com/api
2. Создай standalone-приложение
3. Получи сервисный токен в Настройках → Сервисный ключ
4. Добавь в `bot/.env`:
   ```
   VK_SERVICE_TOKEN=vk1.a.1234567890...
   ```

### MAX Bot API

1. Зайди на https://business.max.ru/self (требует юрлицо/ИП/самозанятого)
2. Создай бота, получи токен
3. Подпишись на каналы через `parse_max.py --subscribe @channel_name`
4. Добавь в `bot/.env`:
   ```
   MAX_BOT_TOKEN=...
   ```

## Мониторинг и логи

```bash
# Логи в реальном времени
sudo journalctl -u benzin-tg-watcher -f
sudo journalctl -u benzin-vk-parser -f

# Или файлы
tail -f /var/log/benzin-tg.log
tail -f /var/log/benzin-vk.log

# Статистика через API
curl https://benzin-ryadom.onrender.com/api/admin/stats
```

## Безопасность

```bash
# 1. Firewall — открой только SSH
sudo firewall-cmd --permanent --zone=public --add-service=ssh  # Oracle Linux
sudo firewall-cmd --reload
# или:
sudo ufw allow OpenSSH        # Ubuntu
sudo ufw enable

# 2. SSH hardening
sudo sed -i 's/#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sudo sed -i 's/PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart sshd

# 3. Автообновления
sudo dnf install dnf-automatic -y
sudo systemctl enable --now dnf-automatic.timer
```

## Мониторинг uptime (опционально)

Бесплатные сервисы:
- **UptimeRobot** (https://uptimerobot.com) — 50 мониторов бесплатно
- **Healthchecks.io** (https://healthchecks.io) — 20 мониторов бесплатно
- **BetterStack** (https://betterstack.com) — 10 мониторов

Настройка: добавь в cron `curl https://hc-ping.com/your-uuid` каждые 5 мин. Если запрос не приходит — алерт.

## Бюджет

| Ресурс | Бесплатно | После триала |
|--------|-----------|--------------|
| Oracle VM.Standard.E2.1.Micro | навсегда | — |
| Oracle VM.A1.Flex (4 CPU, 24GB) | навсегда (если доступен) | — |
| Домен (опционально) | ~500₽/год | — |
| **Итого** | **0₽/месяц** | **~42₽/мес** |

## Альтернативы (если Oracle не даёт VM)

### Fly.io (тоже бесплатно)

```bash
# Установка flyctl
curl -L https://fly.io/install.sh | sh

# Регистрация
fly auth signup

# В папке проекта
fly launch  # создаст fly.toml
fly deploy

# Или для cron-job
fly machines create --schedule daily
```

### Google Cloud Free Tier

1. https://cloud.google.com/free
2. Создай e2-micro VM (us-west1, us-east1, или us-central1 — там бесплатно)
3. Те же шаги, что для Oracle

## Troubleshooting

### "Out of capacity" в Oracle

Oracle Free Tier VM.A1.Flex часто "out of capacity" — пишет "Out of host capacity in this availability domain". Решения:
1. Попробуй другой регион (Frankfurt, Amsterdam, etc.)
2. Попробуй меньший shape (VM.Standard.E2.1.Micro — всегда есть)
3. Retry через час

### "Permission denied (publickey)"

SSH ключ не настроен:
```bash
ssh-copy-id -i ~/.ssh/id_rsa.pub oracle@<IP>
```

### Telethon "FloodWaitError"

Telegram throttling — подожди и retry. В Telethon есть `flood_sleep_threshold`.

### VK API rate limit

VK: 3 req/sec на токен. Используй задержки в коде (`await asyncio.sleep(0.34)`).
