# Контекст проекта «Бензин рядом»

## Что это
Бот для Telegram и VK, который помогает водителям находить АЗС с наличием топлива, ценами, очередями, лимитами и запретами на канистры.
Парсит данные из 12+ источников (fuelprice.ru, gdebenz.ru, azslive.ru, benzinmap.ru, ishubenzin.ru, benzin-status.tech, TG-каналы, VK-группы, 2ГИС, сети АЗС, Яндекс.Заправки).
Пользователи могут оставлять отчёты о наличии/ценах/лимитах/канистрах/очередях/качестве.
Платформы: TG бот, VK бот, TG Mini App, Standalone Android APK, Flutter приложение (Android + iOS).

## Технологии
- Python 3.12, aiogram 3.7+ (TG), vkbottle (VK)
- PostgreSQL через Supabase (prod), SQLite (dev)
- aiohttp API для Mini App
- Хостинг: Render Free (Frankfurt) — только Web Service
- Cron: GitHub Actions `.github/workflows/cron-parsers.yml` (каждый час)
- Бот: @benzyn_ryadom_bot (TG), vk.com/benzyn_ryadom (VK community ID: 239975253)
- Flutter 3.44.6 — кроссплатформенное мобильное приложение
- Capacitor — Standalone Android APK (обёртка WebView)

## Структура проекта
```
бензин рядом/
├── bot/                    # Основной код ботов
│   ├── main.py             # Точка входа — запускает TG + VK + API
│   ├── handlers.py         # TG бот — все обработчики
│   ├── vk_bot.py           # VK бот — longpoll
│   ├── vk_callback.py      # VK бот — callback webhook
│   ├── db.py               # БД слой — все запросы, миграции
│   ├── api.py              # HTTP API для Mini App + Flutter
│   ├── keyboards.py        # TG клавиатуры
│   ├── vk_keyboards.py     # VK клавиатуры
│   ├── utils.py            # Форматирование станций, get_main_status()
│   ├── config.py           # Настройки из .env
│   ├── messages.py         # Тексты сообщений
│   ├── push_worker.py      # Push-уведомления + ежемесячный cron тиров
│   ├── channel_poster.py   # Постинг в канал
│   ├── yoomoney_pay.py     # YooMoney оплата
│   ├── yoomoney_worker.py  # Polling worker
│   ├── premium_texts.py    # Тексты фич
│   ├── announcements.py    # Тексты анонсов
│   └── .env                # Секреты (не в git)
├── scripts/                # Парсеры (12 источников)
├── db/
│   ├── schema.sql          # Схема PostgreSQL
│   └── schema_sqlite.sql   # Схема SQLite
├── miniapp/                # Vanilla JS Mini App (TG)
│   ├── app.js              # Основной код
│   ├── index.html          # UI
│   ├── style.css           # Стили + Premium UI
│   ├── premium-catalog.js  # Каталог фич
│   └── premium-ui.js       # PremiumUI namespace
├── android-app/            # Standalone APK (Capacitor WebView)
├── flutter-app/            # Flutter кроссплатформенное приложение
│   ├── lib/
│   │   ├── main.dart           # Точка входа: Splash → Registration → MainScreen
│   │   ├── screens/
│   │   │   ├── splash_screen.dart       # Анимированный сплеш ⛽
│   │   │   ├── registration_screen.dart # Регистрация/вход (имя + пароль + VK/TG)
│   │   │   ├── main_screen.dart         # BottomNav: Карта/Поиск/Маршруты/Профиль
│   │   │   ├── map_screen.dart          # flutter_map + OpenStreetMap
│   │   │   ├── search_screen.dart       # Поиск по городу/топливу/сети/цене
│   │   │   ├── station_detail_screen.dart # Детали АЗС
│   │   │   ├── premium_screen.dart      # 4 тарифа + Founder Pack
│   │   │   ├── profile_screen.dart      # Профиль + статистика + рефералы
│   │   │   ├── routes_screen.dart       # Поиск по трассам
│   │   │   └── settings_screen.dart     # Настройки
│   │   ├── models/
│   │   │   ├── station.dart     # Модель станции (statuses[] array)
│   │   │   └── user.dart        # UserProfile модель
│   │   ├── services/
│   │   │   ├── api_service.dart  # 40+ API эндпоинтов
│   │   │   ├── location_service.dart  # GPS
│   │   │   └── storage_service.dart   # SharedPreferences
│   │   ├── widgets/
│   │   │   ├── station_card.dart
│   │   │   ├── premium_card.dart
│   │   │   ├── referral_card.dart  # Elite+ gate
│   │   │   └── report_sheet.dart
│   │   ├── config/
│   │   │   ├── api.dart          # API base URL + 56 городов
│   │   │   └── theme.dart       # Тёмная тема
│   │   └── ...
│   ├── android/             # Android конфигурация (minSdk 21)
│   ├── ios/                 # iOS конфигурация
│   └── play_store/          # Материалы для Google Play
├── .github/workflows/
│   └── cron-parsers.yml    # GitHub Actions cron (каждый час)
├── context/                # Документация
├── render.yaml             # Конфиг Render
└── requirements.txt
```

## API эндпоинты
- `GET /api/health` — health check
- `GET /api/admin/stats` — статистика
- `GET /api/stations?lat=&lon=&fuel=&max_price=&network=&limit=` — ближайшие АЗС
- `GET /api/stations/by-city?city=&fuel=&max_price=&network=` — АЗС по городу
- `GET /api/stations/{id}` — детали АЗС
- `GET /api/search?q=` — поиск
- `GET /api/price-history/{id}` — история цен
- `GET /api/station-prices/{id}` — текущие цены
- `GET /api/cities?q=` — поиск городов
- `GET /api/routes?q=` — поиск трасс
- `GET /api/routes/{id}/stations?limit=50` — АЗС на трассе
- `POST /api/reports` — создание отчёта
- `POST /api/reviews` — отзыв
- `POST /api/user/register` — регистрация (name, password, device_id, vk_link?, tg_link?)
- `POST /api/user/login` — вход (name, password)
- `POST /api/premium/create-payment` — создание платежа YooMoney
- `POST /api/account/link/create` — привязка аккаунтов
- `POST /api/account/link/use` — применение кода привязки
- `GET /api/user/stats` — статистика пользователя (reports, confirmed, badges, reputation, city)
- `GET /api/referral/tier` — тир реферера + прогресс
- `GET /api/referral/commission-rates` — все ставки комиссий
- `GET /api/referral/selling-texts` — продающие тексты для копирования
- `GET /api/referral/leaderboard` — топ рефереров

## Автор API ID — Критическое!
- Все API запросы используют параметр `telegram_id` (не `user_id`!)
- Flutter хранит `telegramId` отдельно от `userId`
- `ApiService.setUserId(telegramId)` — устанавливает ID для всех запросов
- `api.dart: timeout=15s, longTimeout=60s` (для Render cold start)

## Premium подписки

### Тарифы
- **Эконом**: 100₽/мес
- **Стандарт**: 250₽/мес
- **Элит**: 500₽/мес
- **Founder Pack**: 1990₽ (навсегда, 200 мест)

### Реферальная программа (обновлено 20.07.2026)
- **1-й уровень** (твои рефералы): 50/55/60/65% в зависимости от тира
- **2-й уровень** (рефералы твоих рефералов): 5% для всех Elite/Founder
- **3-й уровень** (3-е звено): 3% только для топ-3 рефереров
- **Тиры**: basic (50%), ambassador (55%, 50+ активных), top_ref (60%, 100+), legend (65%, 200+)
- **Топ-3 месяца**: 70% комиссии для всех трёх
- **Приглашённый** получает 15% скидку на первую покупку
- Реферальная ссылка: `t.me/benzyn_ryadom_bot?start=ref_CODE`
- Реферальный баланс: вывод от 100₽, админ одобряет

### Cron для рефералов (ежемесячный)
- 1-е число каждого месяца: расчёт топ-3, пересчёт тиров, уведомления
- Реализовано в `push_worker.py:run_monthly_referral_cron()`

## Flutter — Статус (20.07.2026)

### Рабочее:
- Сплеш с анимацией ⛽ (2.5 сек)
- Регистрация (имя + пароль) / Вход
- Карта (flutter_map + OpenStreetMap, цветные маркеры)
- Поиск (город, топливо, сеть, цена)
- Детали АЗС (статусы наличия/цены)
- Профиль (имя, статистика, премиум бейдж, реферальная карточка)
- Премиум (4 тарифа, Founder Pack, таблица сравнения)
- Настройки (город, топливо по умолчанию)
- Маршруты (поиск по трассам)
- Кнопка «Выйти» в профиле

### Требует доработки:
- VK ссылка → поиск существующего VK-аккаунта (backend готов, нужен тест)
- Подключение реальных цен/наличия из БД в поиск
- Push-уведомления
- Оффлайн-режим

## Секреты
- `BOT_TOKEN` — TG бот
- `VK_TOKEN` — VK group token
- `DATABASE_URL` — PostgreSQL Supabase
- `PARSE_API_KEY` = `benzin-parse`
- `YOOMONEY_TOKEN` — OAuth access token
- `YOOMONEY_RECEIVER` — номер кошелька
- `ADMIN_USERNAMES` = `darkt30`
- `CHANNEL_CHAT_ID` = `-1004357499897`

## Известные проблемы
1. **Render Free tier** — иногда не рестартит, нужен "Clear build cache & deploy"
2. **TelegramConflictError** — две инстанции бота при деплое (30-60 сек, само проходит)
3. **Flutter регистрация** — если VK ссылка не находит существующий аккаунт, создаётся новый без премиум
