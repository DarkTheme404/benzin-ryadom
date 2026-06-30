# Парсеры — настройка и запуск

Этот документ — инструкция по запуску всех парсеров проекта «Бензин рядом».
Парсеры собирают данные о ценах и наличии топлива на АЗС из разных источников.

## Сводка источников

| Источник | Тип | Бесплатно? | Где запускать | Сложность |
|----------|-----|-----------|---------------|-----------|
| **fuelprice.ru** | HTTP | ✅ | Render cron | Готово |
| **benzin-price.ru** | Playwright | ✅ | **GitHub Actions** | Готово |
| **2ГИС** | HTTP API | 1К/день | Локально/VPS | Нужен ключ |
| **Telegram-каналы** | Userbot | ✅ | Локально/VPS | Нужен Telethon |
| **Telegram-боты** | Bot API | ✅ | Наш бот | Готово |
| **VK паблики** | Web/API | ✅ | Локально/VPS | Опц. токен |
| **MAX каналы** | Bot API | ✅ | Локально/VPS | Нужен токен |
| **Яндекс.Заправки** | Mobile API | ✅ | Локально | Исследовать |

---

## 1. benzin-price.ru (основной) — GitHub Actions

**Готово из коробки.** Workflow `.github/workflows/benzin-price.yml` парсит через Playwright и загружает в backend.

### Настройка (один раз)

1. **Сгенерируй IMPORT_API_KEY**:
   ```bash
   openssl rand -hex 32
   # например: a3f1b8c2d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1
   ```

2. **Добавь в `bot/.env`** (на Render):
   ```
   IMPORT_API_KEY=a3f1b8c2d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1
   BACKEND_URL=https://benzin-ryadom.onrender.com
   ```

3. **Добавь secrets в GitHub**:
   - Открой `Settings → Secrets and variables → Actions → New repository secret`
   - `IMPORT_API_KEY` = то же значение
   - `BACKEND_URL` = `https://benzin-ryadom.onrender.com`

4. **Запусти**:
   - Вкладка Actions → `benzin-price.ru Parser` → Run workflow
   - Или подожди — он автоматически стартует каждый день в 03:00 UTC

5. **Для всех регионов разом**:
   - Вкладка Actions → `benzin-price.ru ALL REGIONS` → Run workflow
   - 20 job'ов параллельно (max 5 одновременно), каждый берёт свой регион

### Параметры workflow

- `region`: `1` (Москва), `2` (СПб), ... или `all` (все)
- `limit`: макс. АЗС на регион (default 200)
- `skip_upload`: только собрать JSON без отправки в API

### Лимиты GitHub Actions

- **Public repo**: безлимитно
- **Private repo**: 2000 мин/мес бесплатно
- Один job с 200 АЗС ≈ 5 мин. 20 регионов × 100 АЗС = ~30 мин (параллельно).

### Стоимость Playwright

- Один job: 200 АЗС × ~1.5 сек = 5 мин
- Chromium setup: 1-2 мин
- Upload JSON: <1 сек

---

## 2. fuelprice.ru — Render cron (уже работает)

Уже подключён в `scripts/orchestrator.py`. Запускается по cron на Render.

Файл: `scripts/orchestrator.py` (SOURCES["fuelprice"])

---

## 3. Telegram-каналы — Telethon (опционально)

**⚠️ Userbot — нарушает ToS Telegram на свой страх и риск.**

### Настройка

1. Зайди на https://my.telegram.org/apps
2. Создай приложение → получи `api_id` и `api_hash`
3. Добавь в `bot/.env`:
   ```
   TG_API_ID=12345678
   TG_API_HASH=abcdef0123456789abcdef0123456789
   ```
4. Первый запуск попросит ввести телефон и код:
   ```bash
   python scripts/parse_tg_channels.py --channel benzin_price_ru
   # Создаст файл сессии: scripts/.session/benzin_user.session
   ```
5. Дальше запускается автоматически. Сессия сохраняется.

### Каналы для парсинга

Добавь в `scripts/parse_tg_channels.py`:
```python
CHANNELS = [
    "@benzin_price_ru",
    "@azs_price",
    "@toplivoprice",
    # ...
]
```

### Ограничения

- Telethon — Userbot, может забанить аккаунт
- Лимит Telegram API: ~30 req/sec (для чтения истории)
- Бот API НЕ МОЖЕТ читать каналы — это техническое ограничение

---

## 4. Telegram-боты конкурентов — Bot API (уже работает)

Уже реализовано в `bot/handlers.py:handle_bot_message`. Перехватывает сообщения от других ботов.

### Что нужно

- Боты-конкуренты должны писать нашему боту (например, через inline-режим или напрямую)
- Или мы подписаны на их каналы как канал-агрегатор

### Подключение ботов-конкурентов

Нужно добавить их в `bot/handlers.py`:
```python
COMPETITOR_BOTS = {
    "@benzin_price_bot": "BenzinPrice",
    "@azsprice_bot": "AzsPrice",
    "@toplivo_bot": "Toplivo",
}
```

---

## 5. VK паблики — Web/API

Файл: `scripts/parse_vk.py`

### Два режима

**Web (по умолчанию)**: парсит m.vk.com без авторизации
```bash
python scripts/parse_vk.py --groups benzinclub,azsobj --limit 200
```

**API (лучше)**: через VK API с сервисным токеном
1. Создай standalone-приложение: https://dev.vk.com/api
2. Получи сервисный токен
3. Добавь в `bot/.env`: `VK_SERVICE_TOKEN=...`
4. Запусти:
   ```bash
   python scripts/parse_vk.py --api-mode --groups benzinclub
   ```

### Подключение к оркестратору

Уже подключён в `scripts/orchestrator.py` (parse_vk_all_groups).

---

## 6. MAX (национальный мессенджер) — Bot API

Файл: `scripts/parse_max.py`

### Настройка

1. Зарегистрируй бота: https://business.max.ru/self (требует юрлицо/ИП/самозанятого)
2. Получи токен
3. Добавь в `bot/.env`: `MAX_BOT_TOKEN=...`
4. Подпишись на каналы:
   ```bash
   python scripts/parse_max.py --subscribe @benzin_channel
   ```
5. Запусти парсинг:
   ```bash
   python scripts/parse_max.py --all-channels --hours 24
   ```

### API

Документация: https://platform-api2.max.ru
- `GET /chats/{link}` — инфо о канале
- `GET /messages?chat_id=N&count=100` — последние сообщения
- `POST /subscriptions` — webhook (для real-time)

---

## 7. 2ГИС — HTTP API

Файл: `scripts/parse_2gis.py`

### Настройка

1. Получи API ключ: https://dev.2gis.ru (бесплатно 1 000 req/день)
2. Добавь в `bot/.env`: `TWO_GIS_API_KEY=...`
3. Запусти:
   ```bash
   python scripts/parse_2gis.py --city Иваново --fuel 92
   ```

---

## 8. Яндекс.Заправки — research

**Не реализовано полностью** — endpoint нестабильный. Если получится — парсер будет выдавать реальные цены с АЗС Яндекс.Заправок (крупнейший агрегатор в РФ).

### Где искать

- reverse engineering мобильного приложения
- endpoint может быть устаревшим
- ищите на 4PDA/Stack Overflow

---

## Итого: что сделать прямо сейчас

1. ✅ benzin-price.ru → настроить GitHub Actions (см. секцию 1)
2. ⏳ Получить Yandex Geocoder API ключ (для enrich 18 928 АЗС)
3. ⏸ Дождаться Yandex.Заправки research
4. ⏸ Зарегистрировать MAX бот (если нужно)
