# Контекст проекта «Бензин рядом»

## Что это
Бот для Telegram и VK, который помогает водителям находить АЗС с наличием топлива, ценами, очередями, лимитами и запретами на канистры.
Парсит данные из 12+ источников (fuelprice.ru, gdebenz.ru, azslive.ru, benzinmap.ru, ishubenzin.ru, benzin-status.tech, TG-каналы, VK-группы, 2ГИС, сети АЗС, Яндекс.Заправки).
Пользователи могут оставлять отчёты о наличии/ценах/лимитах/канистрах/очередях/качестве.

## Технологии
- Python 3.12, aiogram 3.7+ (TG), vkbottle (VK)
- PostgreSQL через Supabase (prod), SQLite (dev)
- aiohttp API для Mini App
- Хостинг: Render Free (Frankfurt) — только Web Service
- Cron: GitHub Actions `.github/workflows/cron-parsers.yml` (каждый час)
- Бот: @benzyn_ryadom (TG), vk.com/benzyn_ryadom (VK community ID: 239975253)

## Структура проекта
```
бензин рядом/
├── bot/                    # Основной код ботов
│   ├── main.py             # Точка входа — запускает TG + VK + API
│   ├── handlers.py         # TG бот — все обработчики (~3000 строк)
│   ├── vk_bot.py           # VK бот — longpoll (~1400 строк)
│   ├── vk_callback.py      # VK бот — callback webhook (~1100 строк)
│   ├── db.py               # БД слой — все запросы (~2860 строк)
│   ├── api.py              # HTTP API для Mini App (~1900 строк)
│   ├── keyboards.py        # TG клавиатуры
│   ├── vk_keyboards.py     # VK клавиатуры
│   ├── utils.py            # Форматирование станций, get_main_status()
│   ├── config.py           # Настройки из .env
│   ├── messages.py         # Тексты сообщений
│   ├── push_worker.py      # Push-уведомления подписчикам
│   ├── channel_poster.py   # Постинг в канал
│   ├── .env                # Секреты (не в git)
│   └── benzin.db           # SQLite (dev)
├── scripts/                # Парсеры
│   ├── parse_fuelprice.py  # fuelprice.ru (~20K отчётов)
│   ├── parse_gdebenz.py    # gdebenz.ru (~30K отчётов)
│   ├── parse_ishubenzin.py # ishubenzin.ru
│   ├── parse_tg_channels.py# Telegram каналы (Telethon)
│   ├── parse_benzinmap.py  # benzinmap.ru (62 региона, лимиты/канистры)
│   ├── parse_azslive.py    # azslive.ru (26K АЗС, наличие)
│   ├── parse_benzin_status_tech.py  # benzin-status.tech Mini App
│   ├── parse_vk_groups.py  # VK группы (94 шт)
│   ├── parse_networks.py   # Лукойл/Газпромнефть/Роснефть/Татнефть/Башнефть
│   ├── parse_yandex_fuel.py# Яндекс.Заправки
│   ├── orchestrator.py     # Оркестратор — SOURCES dict
│   ├── gdebenz_areas_all.py# 4233 bbox городов
│   └── ...
├── db/
│   ├── schema.sql          # Схема PostgreSQL
│   └── schema_sqlite.sql   # Схема SQLite
├── miniapp/                # Vanilla JS Mini App
│   ├── app.js              # Основной код
│   ├── index.html          # UI
│   └── style.css
├── mini-app/               # React/TypeScript Mini App
│   └── src/api.ts          # postReport/postReview
├── .github/workflows/
│   ├── cron-parsers.yml    # GitHub Actions cron (каждый час)
│   └── ...
├── context/                # Документация
├── render.yaml             # Конфиг Render
└── requirements.txt
```

## API эндпоинты
- `GET /api/health` — health check
- `GET /api/admin/stats` — статистика
- `GET /api/reverse-geocode?lat=&lon=` — геокодирование
- `GET /api/stations?lat=&lon=&fuel=&max_price=&network=&limit=` — ближайшие АЗС
- `GET /api/stations/by-city?city=&fuel=&max_price=&network=` — АЗС по городу
- `GET /api/stations/emergency?city=&fuel=` — экстренный поиск
- `GET /api/stations/{id}` — детали АЗС
- `GET /api/search?q=` — поиск
- `GET /api/price-history/{id}` — история цен
- `GET /api/station-prices/{id}` — текущие цены
- `GET /api/station-analytics/{id}` — аналитика (для владельцев)
- `GET /api/premium-status` — статус Premium
- `POST /api/reports` — **создание отчёта** (price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment, fuel_type)
- `POST /api/reviews` — **отзыв** (rating 0-5, comment)
- `POST /api/price-update` — обновление цены
- `POST /api/import_prices` — импорт от парсеров
- `POST/GET /api/parse?key=` — запуск парсеров
- `GET /api/parse-benzin` — benzin-status.tech парсер
- `POST /api/vk/callback` — VK webhook
- `POST /api/vk/test-event` — тест VK
- `GET /api/enrich` — обогащение адресов
- `GET /api/import-osm` — OSM импорт
- `GET /api/logs` — последние логи

## Секреты
- `BOT_TOKEN` — TG бот
- `DATABASE_URL` — PostgreSQL Supabase
- `PARSE_API_KEY` = `benzin-parse`
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING` — для TG парсера
- `ADMIN_USERNAMES` = `darkt30`
- `SUBSCRIBE_CHANNEL_TG` = `benzyn_ryadom`
- `SUBSCRIBE_COMMUNITY_VK` = `239975253`
- `VK_SERVICE_TOKEN` — для VK парсера
- `ALLOWED_ORIGINS` — CORS

В GitHub Actions:
- `PARSE_API_KEY` = `benzin-parse`

## Архитектура отчётов
Все клиенты (TG бот, VK бот, Mini App) пишут в одну таблицу `reports` через единый API `POST /api/reports`:

| Поле | Описание |
|------|----------|
| station_id | FK на stations |
| user_id | FK на users (опционально для парсеров) |
| fuel_type | 92, 95, 98, 100, diesel, lpg, all |
| available | True (есть) / False (нет) / None (кончается) |
| price | Цена за литр в ₽ |
| queue_size | Число машин в очереди |
| has_limit + limit_liters | Общий лимит на заправку |
| limit_per_visit | Лимит за раз |
| limit_daily | Дневной лимит |
| limit_weekly | Недельный лимит |
| canister_ban | Запрет заправки в канистры |
| comment | Текстовый комментарий до 500 символов |
| source | "user", "miniapp", "vk_user", "fuelprice_ru", "azslive", "benzinmap", etc. |

Парсеры устанавливают `source` в имя источника. Пользовательские отчёты — "user", "miniapp", "vk_user".

## Статистика (на 12.07.2026)
- 28,521 станций в БД
- 4,234 уникальных города
- **1,256+ городов с live данными** (было 117)
- **15,833+ live отчётов** (было 18,105)
- **7,867+ станций с live наличием** (было 3,710)
- **398 отчётов с лимитом/канистрами**
- **595 отчётов с очередями**
- **18,846 отчётов с ценами**

### Источники live данных:
- gdebenz.ru: 5,981+ станций (наличие) — **главный источник наличия, покрытие 4,233 городов**
- fuelprice.ru: 1,729 (наличие, цены)
- azslive.ru: 1,026 (наличие, очереди)
- benzin-status.tech: 27 (цены, лимиты, канистры)
- ishubenzin.ru: 2 (наличие, цены)

## Cron
- GitHub Actions: `.github/workflows/cron-parsers.yml`
- Расписание: каждый час (`0 * * * *`)
- Содержимое: будит Render API + запускает `GET /api/parse?key=$PARSE_API_KEY`
- Render cron service НЕ используется (только Web Service)
