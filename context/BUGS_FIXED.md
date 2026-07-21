# Исправленные баги

## 20.07.2026 — Реферальная система с тирами

### НОВЫЕ ФИЧИ
- **Тиры рефереров**: basic (50%), ambassador (55%), top_ref (60%), legend (65%)
- **Топ-3 месяца**: 70% комиссии
- **Multi-level комиссии**: 1-й (50-65%), 2-й (5%), 3-й (3% для топ-3)
- **Cron**: ежемесячный расчёт топ-3 + пересчёт тиров + уведомления
- **API**: /api/referral/tier, /api/referral/commission-rates, /api/referral/selling-texts
- **TG Bot**: реферальная секция с тирами, калькулятором, продающими текстами, обучением
- **VK Bot**: реферальная секция + команды /selling, /training
- **MiniApp**: тиры + калькулятор + продающие тексты + обучение

### ИСПРАВЛЕНИЯ
- **push_worker.py — broken _fetch import**: `run_monthly_referral_cron` использовал `_fetch` и `USE_SQLITE` из модульного уровня, но они не были импортированы. Исправлено на локальные импорты.
- **push_worker.py — circular db reference**: `db.get_commission_rate()` вызывался через `db.`, хотя `db` уже был импортирован. Заменено на `get_commission_rate()` из локального импорта.
- **api.py — missing REFERRAL_TIER_NAMES**: handler `handle_referral_tier` использовал `REFERRAL_TIER_NAMES` без импорта. Добавлен импорт.
- **api.py — handle_referral_tier handler registration**: эндпоинт `/api/referral/tier` был добавлен в handlers, но не зарегистрирован в `register_routes()`. Добавлена регистрация.

## 19.07.2026 — Flutter авторизация + password + referral Elite gate

### КРИТИЧНЫЕ
- **Flutter: API ID mismatch** — Flutter шлёт `telegram_id=<user_id>`, а API ищет по колонке `telegram_id` (разные значения). Вернулись пустые данные, карта пуста, премиум не виден.
  - **Исправление**: register/login возвращают `telegram_id`, Flutter хранит `telegramId` отдельно от `userId`
- **Backend: password_hash колонка не существовала в PostgreSQL** — only SQLite миграция была добавлена. На Render (PG) крашился register/login с 500.
  - **Исправление**: `ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT` в `_create_schema_pg`
- **Backend: bcrypt не устанавливается на Render** — native модуль fails during pip install. Register крашился с ImportError.
  - **Исправление**: заменён на `hashlib.pbkdf2_hmac` (stdlib, no compilation)
- **Flutter: _storage.userId = userId** — был вызов несуществующего метода `setUserId()`. Крашил register.
  - **Исправление**: используется setter `_storage.userId = userId`
- **Flutter: Path.moveTo/lineTo не найден** — `flutter_map` использует `ui.Path`, не `dart:ui Path`. 5 compile errors в map_screen.dart.
  - **Исправление**: импорт `dart:ui as ui`, `_TrianglePainter` использует `ui.Path()`

### ВЫСОКИЕ
- **Flutter: registration_screen.dart — merged register/login** — edit失误 deleted _login() method entirely.
  - **Исправление**: _login() method восстановлен отдельно
- **Flutter: mounted check отсутствовал** — `setState()` вызывался после `dispose()` при быстрой навигации.
  - **Исправление**: добавлены `if (!mounted) return;` перед каждым `setState()`
- **Flutter: Render timeout** — стандартный 15s timeout не хватало для cold start Render Free (~30-60s). Register/logout показывали "Ошибка сети".
  - **Исправление**: `ApiConfig.longTimeout = 60s` для register/login
- **Mini App: referral commission без проверки тарифа** — любой реферер получал 50% комиссии, даже Free.
  - **Исправление**: `record_referral_commission()` проверяет referrer tier (Elite/Founder only)
- **Mini App: referral text** — не было указания на Elite ограничение.
  - **Исправление**: добавлено "Доступно для тарифов Элит и выше"

### НИЗКИЕ
- **Flutter: unused imports** — `dart:convert`, `dart:ui`, `../config/api.dart`. Warning.
  - **Исправление**: удалены
- **Flutter: `__` variable** — `SplashScreen` использовал `__` вместо `_c`. Info warning.
  - **Исправление**: переименовано

## 12.07.2026 — 13 багов + Render cron + unified reporting

### КРИТИЧНЫЕ
- **`utils.py` — добавлен `get_main_status()`** (vk_callback.py крашился без него)
- **`vk_callback.py` — добавлен `import db`** (NameError на `db.USE_SQLITE`)
- **`handlers.py:2573` — Mini App отчёты**: `get_or_create_user` возвращает user_id (не telegram_id). Был перепутан → user_id=None
- **`handlers.py:2874,2891,2902` — analytics**: использовал `owner_stations.id` вместо `station_id` → аналитика владельцев сломана

### ВЫСОКИЕ
- **`api.py:1447` — handle_parse_benzin**: `db.API_MODE = True` не сбрасывался. Добавлен `finally: db.API_MODE = False`
- **`api.py:1410,1674` — `_parsers_running`**: мог застрять навсегда при exception. Добавлен `try/finally`
- **`api.py:1113` — rate limit**: было hardcoded `30`, заменено на `RATE_LIMIT_POST` (10)
- **`db.py:2207-2272` — дубликаты `get_source_priority`/`calculate_confidence`**: второй overwrote первый. Удалён дубликат
- **`db.py:_execute` — `returning=True` без `RETURNING id`**: для PG `fetchrow` возвращал None. Добавлен авто-appending
- **`handlers.py:1726` — `/set_ad off`**: был недостижим. Проверка `off` вынесена вперёд
- **`miniapp/app.js:430` — `state.region`**: переменная не существует, должно быть `state.cityRegion`
- **`db.py:init_db/close_db` — не уважали `API_MODE`**: парсеры из API закрывали общий пул

### GitHub Actions cron
- **`f394009`** — создан `.github/workflows/cron-parsers.yml`
- **`3eaef30`** — улучшен с retry и 60s timeout
- **`ec33fdb`** — fix: "already running" treated as success
- **`bc633de`** — fix: regex `\s*` для JSON

## 03.07.2026 — Глубокий аудит кодовой базы

### КРИТИЧНЫЕ
1. **callback.message.from_user = бот** → `_ensure_callback_user(callback)` + `callback.from_user.id`
2. **VK _send() без аргумента msg** → `_send(msg, text, keyboard)`
3. **VK "Я владелец" ловился wrong handler** → проверка `uid not in _owner_waiting_role`
4. **VK "Отменить" ≠ "Отмена"** → `"Отмен" in text`
5. **VK state undefined crash** → `state = _user_state.get(uid, {})`
6. **DB connection leak** → `async with _db.acquire() as conn`
7. **datetime.now() vs tz-aware PG** → `datetime.now(timezone.utc)`
8. **api.py missing imports** → добавлены `is_premium`, `get_premium_info`
9. **api.py _db undefined** → заменено на `db._fetch()`
10. **api.py double init_db** → проверка `_db is None`

### ВЫСОКИЕ
11. **report_start filter** → `F.data.regexp(r"^report:\d+$")`
12. **Diesel "АИ-diesel"** → проверка `fuel == "diesel"` → "Дизель"
13. **enrich_addresses убивал DB pool** → проверка `API_MODE`
14. **Address search по словам** → разбиение на слова
15. **TG FSM handler ordering** → зарегистрирован ПЕРЕД catch-all

### СРЕДНИЕ
16. **Esc/escape_html дублирование**
17. **VK _user_state race condition**
18. **VK memory leak**
19. **api.py CORS wildcard**
20. **PG find_stations_by_name — missing params**
