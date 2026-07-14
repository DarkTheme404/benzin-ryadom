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
│   ├── handlers.py         # TG бот — все обработчики (~3444 строк)
│   ├── vk_bot.py           # VK бот — longpoll (~1400 строк)
│   ├── vk_callback.py      # VK бот — callback webhook (~1429 строк)
│   ├── db.py               # БД слой — все запросы (~3100 строк)
│   ├── api.py              # HTTP API для Mini App (~2133 строк)
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
- `GET /api/cities?q=` — **поиск городов** (кириллица/латиница, транслитерация)
- `GET /api/routes?q=` — **поиск трасс** (М-4, М-7, Р-217, "Дон", "Кавказ")
- `GET /api/routes/{id}/stations?limit=50` — **АЗС на трассе**
- `POST /api/reports` — **создание отчёта** (price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment, fuel_type)
- `POST /api/reviews` — **отзыв** (rating 0-5, comment)
- `POST /api/price-update` — обновление цены
- `POST /api/import_prices` — импорт от парсеров
- `POST/GET /api/parse?key=` — запуск парсеров (12 источников)
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
- 28,959 станций в БД
- 4,234 уникальных города
- **533+ городов с live данными** (было 117)
- **24,978+ live отчётов** (было 18,000)
- **8,634+ станций с live наличием** (было 3,710)
- **398+ отчётов с лимитом/канистрами**
- **595+ отчётов с очередями**
- **18,846+ отчётов с ценами**

### Источники live данных:
- gdebenz.ru: 6,000+ станций (наличие) — **главный источник наличия, 4,233 городов**
- fuelprice.ru: 1,729 (наличие, цены)
- azslive.ru: 1,026 (наличие, очереди)
- news_kommersant/lenta/vedomosti/interfax: 41 (новости про топливо)
- benzin-status.tech: 27 (цены, лимиты, канистры)
- ishubenzin.ru: 2 (наличие, цены)

### Парсеры (12 источников):
1. **gdebenz.ru** — 4,233 bbox городов (главный)
2. **fuelprice.ru** — топ-12 городов
3. **azslive.ru** — 15 bbox регионов
4. **benzinmap.ru** — 62 региона (лимиты/канистры)
5. **benzin-status.tech** — 80 городов (цены/лимиты)
6. **ishubenzin.ru** — 30 городов
7. **yandex_fuel** — 16 крупных городов
8. **parse_news** — RSS Kommersant/Lenta/Vedomosti/Interfax
9. **parse_vk** — VK API поиск
10. **parse_tg_channels** — 303 TG канала
11. **parse_networks** — 5 сетей АЗС
12. **parse_yandex_maps** — POI (Playwright)

## Cron
- GitHub Actions: `.github/workflows/cron-parsers.yml`
- Расписание: каждый час (`0 * * * *`)
- Содержимое: будит Render API + запускает `GET /api/parse?key=$PARSE_API_KEY`
- Render cron service НЕ используется (только Web Service)

## Маршруты (трассы) — 13.07.2026
- **39 федеральных/региональных трасс РФ** (М-1...М-12, Р-21...Р-404, М-17/18 Крым)
- **71,554 связей АЗС-трасса** (через bbox-привязку)
- Поиск поддерживает кириллицу/латиницу ("М-4" = "M-4")
- UI во всех 3 каналах: TG бот, VK бот, Mini App (вкладка "Трассы")
- API: `/api/routes?q=`, `/api/routes/{id}/stations`
- **Mini App**: вкладка "Трассы" — список всех трасс с группировкой (федеральные/региональные/другие), клик → поиск АЗС, кнопка "← Назад к списку"
- **JS архитектура**: `loadRoutesList()` и `doRouteSearch()` на IIFE-уровне (не внутри bindEvents)

## Обогащение адресов — 12.07.2026
- **Photon (komoot.io)** reverse geocoding — основной провайдер
- **Semaphore=3, sleep=0.5** — ~0.3 addr/s, 0 ошибок (было Semaphore=10, sleep=0.12 → 40% 503)
- **Retry + exponential backoff** — при 503/429: 2s, 4s, 8s
- **Фильтр POI-имён** — osm_value=fusion → None, «кавычки» → None (бренды АЗС: "«Дон»", "«Холмогоры»")
- **Success rate**: ~27% (сельские районы без street addressing), ~50%+ в городах
- **API**: `GET /api/enrich?key=benzin-parse&limit=500` (max 5000, default 500)
- **Запуск**: Render API в фоне через `asyncio.create_task`

## Premium подписки — 13.07.2026
- **3 тарифа**: Эконом 100₽, Стандарт 250₽, Элит 500₽ (все /мес)
- **Оплата**: YooMoney P2P (Quickpay формы)
- **Модуль `bot/yoomoney_pay.py`**: Quickpay URL, operation_history polling
- **Polling worker `bot/yoomoney_worker.py`**: каждые 5 сек проверяет pending payments
- **Таблицы**: `premium_users`, `premium_payments` (PG + SQLite)
- **API**:
  - `GET /api/premium/plans` — список тарифов
  - `GET /api/premium/status?telegram_id=` — статус
  - `GET /api/premium/check?feature=&telegram_id=` — проверка фичи
  - `POST /api/premium/create-payment` — создаёт pending платёж, возвращает YooMoney URL
  - `GET /api/premium/pending` — список ожидающих оплаты
  - `POST /api/premium/activate` — ручная активация (тесты)
  - `POST /api/premium/cancel` — отмена
- **TG бот**: `/premium` → 3 тарифа → кнопка «Купить» → YooMoney URL → «Я оплатил»
- **VK бот**: `/premium` → ссылка на Mini App для оплаты
- **Mini App**: Premium tab с 3 тирами → YooMoney Quickpay URL
- **YooMoney ENV**:
  - `YOOMONEY_TOKEN` — OAuth access token
  - `YOOMONEY_RECEIVER` — номер кошелька (41001...)
- **Для включения**: зарегистрировать кошелёк на yoomoney.ru, создать приложение, получить token

### Фичи по тарифам:
- **Эконом (3)**: price_history, export_csv, offline_map
- **Стандарт (3)**: route_fuel, forecast_7d, fuel_alarm
- **Элит (2)**: anti_traffic, sos_elite

## Посты для соцсетей — 13.07.2026
- **Файл поста**: `context/update_post.md` — полный текст для VK/TG
- **Визуал**: `context/update_post_visual.html` — скриншот 1200×1350px, тёмная тема
- **Содержание**: 39 трасс, 4 288 городов, все федеральные округа с перечислением

## Known Issues
1. **Render Free tier зависает** — иногда не рестартит после коммита. Нужен "Clear build cache & deploy" вручную
2. **TelegramConflictError** при деплое — две инстанции бота пока старая не умрёт (30-60 сек). Само проходит
3. **VK group token** — `groups.search` API (error 27) не работает с group token, нужен user token для автопоиска VK-групп по городам
4. **VK peer_id collision** — VK peer_id сохраняется в `telegram_id` колонку, коллизия с реальными TG ID. Требует миграции: добавить `vk_id` колонку

## Исправленные баги (13.07.2026)
- `audit_middleware` не возвращал `await handler(request)` — 500 на Render health check (commit cd215c9)
- `push_worker` слал самому боту — добавлен фильтр `bot_id` (commit cd215c9)
- `setup_app` не имел `_cors_headers()` — добавлен (commit f800de6)
- `Missing return statement` — исправлен через `return await handler(request)`

## Исправления (12-13.07.2026)
- **PG boolean = integer** — `s.is_active = 1` заменено на `COALESCE(s.is_active, TRUE) = TRUE` в search_cities
- **Decimal not JSON serializable** — asyncpg возвращает Decimal для lat/lon/price; добавлен `json_resp()` с `_json_default` хелпером
- **PG LOWER()** — py_lower() заменено на LOWER() в PG-ветке (py_lower() только для SQLite)
- **Кириллица в URL** — Mini App encodeURI для кириллических запросов к /api/cities
- **Mini App вкладка Трассы** — `setTab()` не обрабатывал `'routes'`; `#tab-routes` был снаружи `.app`; `.main[hidden]` не скрывал main из-за `flex:1`; `loadRoutesList`/`doRouteSearch` были внутри `bindEvents` (недоступны из `setTab`)

## Статистика пользователей (13.07.2026)
- **137** уникальных пользователей за всё время
- **65** активны за 7 дней
- **6** активны за 24 часа
- **9** писали отчёты (конверсия ~6.6%)
- **24** отчёта от пользователей всего
- Разделение TG/VK невозможно — нет колонки `vk_id` (VK peer_id в `telegram_id`)

---

## Premium UX/UI — 14.07.2026 (полная реализация)

### Архитектура Premium

**Слой 1: Backend API (bot/api.py)**
- `GET /api/premium/plans` — список тарифов
- `GET /api/premium/status` — статус подписки (с `get_user_id_by_any` — поддержка linked_id)
- `GET /api/premium/check` — проверка доступа к фиче
- `POST /api/premium/activate` — ручная активация (для тестов/админа)
- `POST /api/premium/cancel` — отмена подписки
- `POST /api/premium/create-payment` — создание платежа YooMoney (с SBP убран)
- `GET /api/premium/pending` — список ожидающих оплат
- `GET /api/account/info` — инфо о linked аккаунтах
- `POST /api/account/link/create` — генерация 6-значного кода
- `POST /api/account/link/use` — использование кода
- `GET /api/stations/{id}/price-history` — **Free: 3 дня/10 точек, Premium: 30+ дней/50 точек + прогноз**
- `GET /api/route/fuel` — **Free: 2 АЗС, Premium: до 30 АЗС + гарантия наличия + рекомендация**
- `GET /api/export/csv` — **Premium only** (HTTP 402 без Premium), CSV с разделителем `;`

**Слой 2: Mini App (JS-модули)**
- `miniapp/premium-catalog.js` — словарь из **8 фич** с icon, name, tagline, savings, urgency
- `miniapp/premium-ui.js` — `PremiumUI` namespace: `loadStatus`, `isFeatureLocked`, `requireFeature`, `showUpsell`, `closeUpsell`, `showToast`, `renderBadge`, `renderBlock`, `renderLockedCard`, `renderUnlockedCard`, `renderHeroCTA`
- `miniapp/app.js` — интеграция: `init()` загружает `PremiumUI`, `loadStationPriceHistory` рисует SVG-график, `loadProfile` показывает tier badge

**Слой 3: UI-компоненты (miniapp/style.css)**
- `.premium-badge` — золотой бейдж с градиентом
- `.feature-locked` — карточка с замочком 🔒
- `.upsell-overlay/.modal` — модальное окно с hero, фичами, тарифами
- `.upsell-feature` — карточка фичи с иконкой, savings, urgency
- `.upsell-tier` — карточка тарифа (featured с золотой рамкой)
- `.upsell-hero` — hero с градиентом `fbbf24 → f59e0b → d97706`
- `.upsell-tier-badge` — "Популярный" бейдж на рекомендуемом тарифе
- `.premium-toast` — всплывающее уведомление о заблокированной фиче
- `.hero-premium-cta` — CTA на главном экране для Free юзеров
- `.feature-card-locked` / `.feature-card` — locked/unlocked варианты
- `.route-fuel-form` / `.route-fuel-result` / `.route-fuel-result-guaranteed` — A→B UI
- `.map-picker-overlay/.modal` — модалка с Leaflet картой

### Каталог фич (8 штук)

| Фича | Тариф | Иконка | Savings | Питч |
|------|-------|--------|---------|------|
| `price_history` | Эконом | 📈 | До 500₽/мес | Видь когда выгоднее заправляться |
| `export_csv` | Эконом | 📊 | 5 часов/мес | Экспорт в Excel |
| `offline_map` | Эконом | 🗺️ | Не потеряешься | Карта без интернета |
| `route_fuel` | Стандарт | 🛣️ | До 1 200₽ на поездку | Маршрут A→B с гарантией топлива |
| `forecast_7d` | Стандарт | 🔮 | До 800₽/мес | Прогноз цен на 7 дней |
| `fuel_alarm` | Стандарт | 🔔 | До 30 мин/день | Топливный будильник (push) |
| `anti_traffic` | Элит | 🚗 | До 3 000₽/мес | Анти-пробка |
| `sos_elite` | Элит | 🆘 | Жизнь | SOS-режим |

### Тарифы (PREMIUM_TIERS_DISPLAY)

| Тариф | Цена | Headline | Pitch | Цвет |
|-------|------|----------|-------|------|
| Эконом | 100₽/мес | Для ежедневных поездок | Экономия до 1 000₽ | #6b7280 |
| Стандарт | 250₽/мес | Для дальних поездок | Экономия до 3 000₽ | #fbbf24 |
| Элит | 500₽/мес | Максимум возможностей | Безопасность + экономия | #8b5cf6 |

### Принципы UX (как заставить купить)

1. **Hero CTA** на главном экране Free-юзера: "💎 Premium: экономь до 3 000₽/мес"
2. **Lock-иконка** 🔒 на каждой заблокированной фиче
3. **Gold-градиент** для всех Premium-элементов (#fbbf24 → #f59e0b)
4. **Social proof**: "2 400+ водителей", "1 800₽ экономия в месяц"
5. **Urgency-тексты**: "Без Premium рискуешь не найти АЗС", "73% Premium юзеров заправляются дешевле"
6. **Savings callouts**: "💰 до 1 200₽ на поездку", "💎 5 часов/мес"
7. **Гарантия** внизу: "🔒 Безопасная оплата через ЮMoney · Можно отменить в любой момент"
8. **Рекомендация** для Premium: "⭐ Лучший выбор" с золотой плашкой
9. **Inline-кнопки** в карточке АЗС с призывом "Купить Premium — от 100₽/мес"

### Оплата: только YooMoney (СБП удалён 13.07.2026)

- `bot/yoomoney_pay.py` — Quickpay формы, operation_history
- `bot/yoomoney_worker.py` — polling каждые 5 сек, автоактивация по `label=benzin-{token}`
- `requirements.txt`: `yoomoney>=2.0.0`
- Env: `YOOMONEY_TOKEN`, `YOOMONEY_RECEIVER`
- Тестовый платёж 100₽ прошёл 13.07.2026, polling активировал premium автоматически

### Привязка аккаунтов TG ↔ VK ↔ MiniApp (14.07.2026)

**db.py:**
- Миграция: `users.linked_telegram_id`, `users.vk_id`, `users.link_code`, `users.link_code_expires_at`
- `create_link_code(telegram_id)` — генерирует 6-значный код (10 мин TTL)
- `get_link_code_info(code)` — получает инфо о коде
- `link_accounts(telegram_id, code)` — привязывает аккаунты
- `get_user_id_by_any(telegram_id)` — ищет по `telegram_id` ИЛИ `linked_telegram_id` (используется всеми premium endpoints)

**API:**
- `POST /api/account/link/create` — принимает `telegram_id` ИЛИ `vk_user_id`
- `POST /api/account/link/use` — принимает `telegram_id` ИЛИ `vk_user_id`
- `GET /api/account/info` — возвращает `telegram_id`, `linked_telegram_id`, `linked_via` (vk/telegram), `vk_id`, `is_premium`, `premium_tier`

**TG бот:**
- `/link` — показывает меню с inline-кнопками (Создать код / Ввести код)
- `/link <code>` — применить код (от VK/MiniApp)
- FSM `LinkStates.waiting_code` для ввода кода
- Кнопка "🔗 Привязать" в нижнем меню

**VK бот:**
- Текст: `link` — создать 6-значный код
- Текст: `link_use <code>` — применить код
- Текст: `link_create` — сразу создать код
- Inline callback: `action="link"` → меню, `action="link_create"`, `action="link_use_prompt"` (FSM)
- Кнопка "🔗 Привязать" в главном меню

**Mini App:**
- В Профиле секция "📱 Мои аккаунты"
- Поля для ввода кода + "✅ Применить код"
- "📱 Telegram ID", "💬 VK ID", "🔗 Привязан к", "💎 Premium" (статус с датой)
- Статус: "✅ Аккаунты привязаны — Premium работает везде"

### Map Picker — выбор точек A/B на карте (14.07.2026)

**Новый модуль в Mini App:**
- `openMapPicker(target, callback)` — открывает модалку с Leaflet картой
- `initPickerMap()` — создаёт карту с draggable маркером
- `setPickerMarker(lat, lon)` — устанавливает/перемещает маркер
- `doPickerSearch()` — геокодинг через Nominatim OpenStreetMap (для всех городов мира)
- `locateUserInPicker()` — определение текущего местоположения через `navigator.geolocation`
- `geocode()` — **двойной fallback**: 1) `/api/search` (по АЗС в базе), 2) **Nominatim OSM** (все города)

**UI:**
- Кнопка "🗺" рядом с каждым полем A/B
- Map Picker Modal: поиск + Leaflet карта + кнопка "Подтвердить"
- Кнопка "📍 Моё местоположение" в правом нижнем углу карты
- Синий маркер "Я" при определении геолокации
- Маркер можно перетаскивать — координаты обновляются

**Маршрут A→B (route_fuel) flow:**
1. Mini App → "🅰️ A→B" в нижней навигации
2. Введи "Москва" в "Откуда" ИЛИ нажми 🗺 → карта → кликни
3. То же для "Куда"
4. Выбери топливо (АИ-95 по умолчанию)
5. "🔍 Найти АЗС по маршруту"
6. Free: 2 АЗС + upsell CTA
7. Premium: 30 АЗС + guaranteed (зелёные) + рекомендация ⭐ + экономия

### Архитектурные решения

- **Единый источник истины** для premium: `bot/db.py` (`PREMIUM_PLANS`, `FEATURE_TIER`, `has_feature()`)
- **Общий модуль** для текстов: `bot/premium_texts.py` (FEATURE_NAMES — human-readable)
- **Один JS namespace** для premium UI: `window.PremiumUI` (с методами)
- **API проверяет premium на сервере** — клиент не может обойти проверку
- **Все premium-функции** в Mini App работают через проверку `PremiumUI.getStatus()`
- **Polling-активация** подписки через `yoomoney_worker.py` каждые 5 сек

---

## Известные проблемы и TODO

### TODO (по приоритету):

1. **TG бот: красивый /premium** — заменить текущий текст на rich UI с фичами
2. **VK бот: красивый /premium** — то же самое
3. **Welcome-экран** с premium teaser (открывается при первом запуске)
4. **Счётчик экономии/streak** в профиле (для мотивации продолжать)
5. **forecast_7d виджет** (прогноз цен на 7 дней) — backend есть (price_history с forecast), нужно UI
6. **fuel_alarm** (push при появлении топлива) — расширить push_worker для premium-only событий
7. **SOS-режим (elite)** — кнопка 🆘, broadcast premium-юзерам в радиусе 50 км
8. **anti_traffic (elite)** — маршрут с учётом пробок (нужен внешний API)
9. **Оффлайн-карта** — кеширование tiles в ServiceWorker

### Известные баги:

- Render Free tier иногда не подхватывает изменения — нужен "Clear build cache & deploy"
- VK peer_id может пересекаться с TG ID в `telegram_id` колонке (legacy)
- `BTN_*` константы разбросаны между `keyboards.py` и `vk_keyboards.py`

---

## Структура Mini App

```
miniapp/
├── index.html              # 683 строки — все экраны
│   ├── screen-home         # Главный экран + hero-premium-cta
│   ├── screen-station      # Карточка АЗС + premium features
│   ├── screen-profile      # Профиль + premium tiers + accounts
│   ├── screen-cities       # Выбор города
│   ├── screen-pick-station # Выбор АЗС
│   ├── screen-route-fuel   # 🆕 Маршрут A→B
│   └── screen-map          # Карта всех АЗС
├── style.css               # 3057 строк — premium UI
├── app.js                  # 2515 строк — основная логика
├── premium-catalog.js      # 8 фич каталог
└── premium-ui.js           # PremiumUI namespace
```

### Структура Backend

```
bot/
├── main.py                 # Точка входа
├── handlers.py             # TG бот (~3600 строк)
├── vk_callback.py          # VK бот callback (~1600 строк)
├── vk_bot.py               # VK бот longpoll
├── vk_keyboards.py         # VK клавиатуры
├── keyboards.py            # TG клавиатуры
├── db.py                   # БД слой (~3700 строк, premium helpers)
├── api.py                  # HTTP API (~2700 строк, premium endpoints)
├── utils.py                # Форматирование
├── messages.py             # Тексты сообщений
├── config.py               # Настройки
├── push_worker.py          # Push-уведомления
├── channel_poster.py       # Постинг в канал
├── yoomoney_pay.py         # YooMoney оплата
├── yoomoney_worker.py      # Polling worker
├── premium_texts.py        # Тексты фич (FEATURE_NAMES)
└── .env                    # Секреты (не в git)
```

### Структура context/

```
context/
├── PROJECT_CONTEXT.md     # Этот файл (главный)
├── update_post.md          # Текст поста для соцсетей
├── update_post.png         # Визуал поста (скриншот)
├── update_post_visual.html # HTML визуала
├── plan.md                 # План доработок (TODO)
└── project_state.json      # Машиночитаемое состояние (для ИИ)
```

### Ключевые ENV переменные

| Переменная | Описание | Обязательно? |
|------------|----------|--------------|
| `BOT_TOKEN` | Telegram bot token | ✅ |
| `VK_TOKEN` | VK group token | ✅ |
| `PARSE_API_KEY` | Ключ для /api/parse | ✅ |
| `DATABASE_URL` | PostgreSQL connection (Supabase) | ✅ для prod |
| `YOOMONEY_TOKEN` | OAuth access token | ✅ для premium |
| `YOOMONEY_RECEIVER` | Номер кошелька (41001...) | ✅ для premium |
| `SUBSCRIBE_CHANNEL_TG` | @benzyn_ryadom | опционально |
| `SUBSCRIBE_COMMUNITY_VK` | 239975253 | опционально |
| `ADMIN_USERNAMES` | darkt30 | опционально |
| `USE_SQLITE` | true (dev) / false (prod) | ✅ |

---

## Метрики (на 14.07.2026)

- **Premium юзеров**: 1 (economy) — нужен marketing
- **Активных TG юзеров**: 137 (с 11.07.2026)
- **Активных VK юзеров**: 5-10 (вручную считал)
- **API endpoints**: 50+
- **Парсеры**: 12 источников
- **Mini App фич реализовано**: 5 из 8 (price_history ✅, export_csv ✅, route_fuel ✅, map_picker ✅, offline_map ❌, forecast_7d partial, fuel_alarm ❌, anti_traffic ❌, sos_elite ❌)
- **Premium tier conversions**: 0.7% (1 из 137) — нужно увеличивать

---

## Связанные документы

- `context/update_post.md` — пост для соцсетей (по обновлениям)
- `context/update_post_visual.html` — визуал поста
- `context/plan.md` — TODO план
- `context/project_state.json` — машиночитаемое состояние
- `bot/premium_texts.py` — тексты фич
- `miniapp/premium-catalog.js` — каталог фич
- `miniapp/premium-ui.js` — UI namespace
- `bot/yoomoney_pay.py` — модуль оплаты
- `db/migrations/2026_07_13_premium.sql` — миграции

