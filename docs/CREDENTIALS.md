# Credentials: как получить API ключи

Этот документ — пошаговая инструкция по получению всех credentials, которые упоминаются в проекте.

## 📱 Telegram API (для Telethon-парсера)

Нужен для: `parse_tg_channels.py` (чтение каналов), `parse_tg_prices.py`.

### Шаг 1: Регистрация приложения

1. Открой **https://my.telegram.org/apps**
2. Войди с **любым** номером телефона (не обязательно рабочий, но лучше реальный)
3. Нажми **"Create new application"**
4. Заполни форму:
   - **App title**: `benzin-ryadom-parser`
   - **Short name**: `benzin`
   - **Platform**: `Desktop`
   - **Description**: `Парсер цен на бензин`
5. Нажми **"Create application"**

### Шаг 2: Получи ключи

На странице приложения увидишь:
- **App api_id** — число (например `12345678`)
- **App api_hash** — строка (например `abcdef0123456789abcdef0123456789`)

### Шаг 3: Добавь в `.env`

В `bot/.env`:
```
TG_API_ID=12345678
TG_API_HASH=abcdef0123456789abcdef0123456789
```

### Шаг 4: Первый запуск (создание session)

```bash
cd /path/to/benzin-ryadom
source venv/bin/activate  # или: python3 -m venv venv && source venv/bin/activate
python scripts/parse_tg_channels.py --channel benzin_price_ru
```

Telethon попросит:
1. **Номер телефона**: введи в международном формате (`+79161234567`)
2. **Код подтверждения**: придёт в Telegram
3. **2FA пароль** (если включён): введи пароль двухфакторной аутентификации

После успешной авторизации создастся файл `scripts/session.session` — **НЕ КОММИТЬ ЕГО!**

### Проверка

```bash
python scripts/parse_tg_channels.py --channel benzin_price_ru
```

Должен просканировать канал и сохранить отчёты.

### ⚠️ Юридические ограничения

- Telethon работает как **Userbot** — это нарушает ToS Telegram
- Используй только для **чтения публичных каналов**
- Не пости от их имени
- Не читай личные сообщения
- Риск: бан аккаунта (используй отдельный номер для парсера!)

---

## 🔵 VK API (для парсинга VK-пабликов)

Нужен для: `parse_vk.py --api` (стабильный режим).

### Шаг 1: Создай приложение

1. Открой **https://dev.vk.com/** → **Мои приложения** → **Создать приложение**
2. Заполни:
   - **Название**: `benzin-ryadom`
   - **Платформа**: `Standalone-приложение`
3. Нажми **"Подключить приложение"**
4. Подтверди SMS-код

### Шаг 2: Получи сервисный токен

1. В настройках приложения → **"Сервисный ключ доступа"**
2. Скопируй токен (формат: `vk1.a.1234567890ABCDEFG...`)

### Шаг 3: Добавь в `.env`

```
VK_SERVICE_TOKEN=vk1.a.1234567890ABCDEFG...
```

### Проверка

```bash
python scripts/parse_vk.py --api --groups avto_benzin,fuel_price --limit 10
```

### Лимиты

- 3 запроса/сек на токен (некоторые методы — больше)
- Без лимита по объёму

---

## 🟢 MAX Bot API (для парсинга MAX-каналов)

Нужен для: `scripts/parse_max.py`.

⚠️ **MAX Bot Platform** — закрытая платформа. Регистрация требует:
- **Юрлицо** (ООО/ИП) или
- **Самозанятого** (нужен ИНН)

### Шаг 1: Регистрация бота

1. Открой **https://business.max.ru/self**
2. Войди через **VK ID** (или создай)
3. Создай бота:
   - **Имя**: `BenzinRyadom Parser`
   - **Описание**: `Парсер цен на топливо`
4. Получи **API-токен**

### Шаг 2: Подпишись на каналы

```bash
python scripts/parse_max.py --subscribe @benzin_channel
```

### Шаг 3: Добавь в `.env`

```
MAX_BOT_TOKEN=...
```

### Проверка

```bash
python scripts/parse_max.py --all --limit 10
```

---

## 🗺 2ГИС API (опционально, для POI АЗС)

Нужен для: `scripts/parse_2gis.py`.

### Шаг 1: Регистрация

1. Открой **https://dev.2gis.ru/**
2. Зарегистрируйся
3. Создай приложение в **Кабинете разработчика**
4. Получи **API ключ**

### Шаг 2: Добавь в `.env`

```
TWO_GIS_API_KEY=...
```

### Лимиты

- 1 000 запросов/день бесплатно
- 100 000/мес на платных тарифах

---

## 🌍 Yandex Geocoder API (для обогащения адресов)

⚠️ **Yandex Geocoder стал платным!** ($10К/год). Не используем.

---

## 🐙 GitHub Secrets (для GitHub Actions)

Нужен для: `.github/workflows/benzin-price*.yml` (Playwright парсер).

### Secrets, которые нужно добавить:

1. Открой репозиторий на GitHub → **Settings → Secrets and variables → Actions → New repository secret**

2. Добавь:
   - **`IMPORT_API_KEY`**: случайная строка 64 символа
     ```bash
     openssl rand -hex 32
     ```
   - **`BACKEND_URL`**: `https://benzin-ryadom.onrender.com`

3. В `bot/.env` на Render добавь **тот же** `IMPORT_API_KEY`.

### Проверка

1. Зайди в **Actions → benzin-price.ru Parser → Run workflow**
2. После завершения — на Render должен появиться новый отчёт (через /api/import_prices)

---

## 📋 Итоговый чеклист

| Сервис | Где получить | Формат ключа | Где использовать |
|--------|--------------|--------------|------------------|
| **Telegram** | my.telegram.org/apps | api_id (int) + api_hash (hex) | `parse_tg_channels.py` |
| **VK** | dev.vk.com | `vk1.a.xxx...` | `parse_vk.py --api` |
| **MAX** | business.max.ru/self | токен из бота | `parse_max.py` |
| **2ГИС** | dev.2gis.ru | 32-символьный ключ | `parse_2gis.py` |
| **GitHub IMPORT_API_KEY** | openssl rand -hex 32 | hex 64 chars | GitHub Actions + .env |
| **GitHub BACKEND_URL** | свой домен | URL | GitHub Actions |

---

## 🚀 Быстрый старт (минимум для запуска)

1. **Telegram** — обязательно (для основного парсера)
2. **GitHub Actions secrets** — обязательно (для benzin-price.ru)
3. Остальные — по желанию

После получения Telegram credentials:
```bash
export TG_API_ID=...
export TG_API_HASH=...
cd /path/to/benzin-ryadom
python scripts/parse_tg_channels.py  # первый запуск с интерактивной авторизацией
```
