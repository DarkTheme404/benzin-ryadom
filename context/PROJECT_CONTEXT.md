# Контекст проекта «Бензин рядом»

## Что это
Бот для Telegram и VK, который помогает водителям находить дешёвый бензин.
Парсит цены с fuelprice.ru, TG-каналов, 2ГИС, ishubenzin. Пользователи могут сообщать о наличии/ценах.

## Технологии
- Python 3.12, aiogram 3.7+ (TG), vk_api (VK)
- PostgreSQL через Supabase (prod), SQLite (dev)
- aiohttp API для Mini App
- Хостинг: Render Free (Frankfurt), VPS 89.108.78.142
- Бот: @benzyn_ryadom (TG), vk.com/benzyn_ryadom (VK community ID: 239975253)

## Структура проекта
```
бензин рядом/
├── bot/                    # Основной код ботов
│   ├── main.py             # Точка входа — запускает TG + VK + API
│   ├── handlers.py         # TG бот — все обработчики (~2700 строк)
│   ├── vk_bot.py           # VK бот — все обработчики (~1300 строк)
│   ├── db.py               # БД слой — все запросы (~2550 строк)
│   ├── api.py              # HTTP API для Mini App (~1335 строк)
│   ├── keyboards.py        # TG клавиатуры
│   ├── vk_keyboards.py     # VK клавиатуры
│   ├── utils.py            # Форматирование станций, карточек
│   ├── config.py           # Настройки из .env
│   ├── messages.py         # Тексты сообщений
│   ├── push_worker.py      # Push-уведомления подписчикам
│   ├── channel_poster.py   # Постинг в канал
│   ├── .env                # Секреты (не в git)
│   └── benzin.db           # SQLite (dev)
├── scripts/                # Парсеры
│   ├── parse_fuelprice.py  # fuelprice.ru
│   ├── parse_gdebenz.py    # gdebenzin
│   ├── parse_ishubenzin.py # ishubenzin
│   ├── parse_tg_channels.py# Telegram каналы
│   ├── enrich_addresses.py # Обогащение адресов (Nominatim/Photon)
│   ├── orchestrator.py     # Оркестратор для cron
│   └── ...
├── db/
│   ├── schema.sql          # Схема PostgreSQL
│   └── schema_sqlite.sql   # Схема SQLite
├── miniapp/                # Веб-приложение
├── context/                # Этот контекст
├── render.yaml             # Конфигурация Render
└── requirements.txt        # Зависимости
```

## Ключевые API эндпоинты
- `GET /api/health` — проверка здоровья
- `GET /api/admin/stats` — статистика парсеров
- `GET /api/search?q=...` — поиск АЗС
- `GET /api/stations?lat=&lon=&fuel=` — ближайшие АЗС
- `GET /api/stations/by-city?city=&fuel=` — АЗС по городу
- `GET /api/stations/emergency?city=` — экстренный поиск
- `POST /api/reports` — создание отчёта
- `POST /api/price-update` — обновление цены
- `GET /api/parse?key=benzin-parse` — запуск парсеров
- `GET /api/enrich?key=benzin-parse` — обогащение адресов
- `GET /api/reverse-geocode?lat=&lon=` — геокодирование

## Секреты (в Render Dashboard)
- `BOT_TOKEN` — TG бот
- `DATABASE_URL` — PostgreSQL Supabase
- `PARSE_API_KEY` = `benzin-parse`
- `TG_API_ID`, `TG_API_HASH`, `TG_SESSION_STRING` — для TG парсера
- `ADMIN_USERNAMES` = `darkt30`
- `SUBSCRIBE_CHANNEL_TG` = `benzyn_ryadom`
- `SUBSCRIBE_COMMUNITY_VK` = `239975253`

## Текущая статистика (03.07.2026)
- 27,084 станции в базе
- 98% с адресами
- fuelprice парсер: ~16K отчётов, OK
- TG парсер: ~5K отчётов, OK
- ishubenzin: ~260 отчётов, OK
- 4 пользователя, 0 organic
