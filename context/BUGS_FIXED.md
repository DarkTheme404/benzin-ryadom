# Исправленные баги

## 12.07.2026 — 13 багов + Render cron + unified reporting

### КРИТИЧНЫЕ
- **`utils.py` — добавлен `get_main_status()`** (vk_callback.py крашился без него)
- **`vk_callback.py` — добавлен `import db`** (NameError на `db.USE_SQLITE`)
- **`handlers.py:2573` — Mini App отчёты**: `get_or_create_user` возвращает user_id (не telegram_id). Был перепутан → user_id=None
- **`handlers.py:2874,2891,2902` — analytics**: использовал `owner_stations.id` вместо `station_id` → аналитика владельцев сломана

### ВЫСОКИЕ
- **`api.py:1447` — handle_parse_benzin**: `db.API_MODE = True` не сбрасывался. Добавлен `finally: db.API_MODE = False`. Также traceback утекал клиенту — убрано
- **`api.py:1410,1674` — `_parsers_running`**: мог застрять навсегда при exception. Добавлен `try/finally` в `_run_parsers` и `_run_import`
- **`api.py:1113` — rate limit**: было hardcoded `30`, заменено на `RATE_LIMIT_POST` (10)
- **`db.py:2207-2272` — дубликаты `get_source_priority`/`calculate_confidence`**: второй overwrote первый (recency_bonus). Удалён дубликат
- **`db.py:_execute` — `returning=True` без `RETURNING id`**: для PG `fetchrow` возвращал None. Добавлен авто-appending `RETURNING id`
- **`handlers.py:1726` — `/set_ad off`**: был недостижим (raw без `|` ловился раньше). Проверка `off` вынесена вперёд
- **`miniapp/app.js:430` — `state.region`**: переменная не существует, должно быть `state.cityRegion`
- **`db.py:init_db/close_db` — не уважали `API_MODE`**: парсеры из API закрывали общий пул. Теперь `init_db` идемпотентна, `close_db` не закрывает при `API_MODE=True`

### GitHub Actions cron
- **`f394009`** — создан `.github/workflows/cron-parsers.yml` (каждый час)
- **`3eaef30`** — улучшен с retry и 60s timeout (Render Free спит)
- **`ec33fdb`** — fix: "already running" treated as success
- **`bc633de`** — fix: regex `\s*` для JSON `"ok": true` (с пробелом)

## Unified Reporting (commit 69919d7)
- `POST /api/reports` расширен: price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment, fuel_type=all
- `POST /api/reviews` — новый endpoint для отзывов с комментарием
- **TG бот**: новые кнопки «Указать цену/лимит/канистры/очередь» после выбора статуса. FSM `ReportExtrasStates`
- **VK бот**: `vk_report_extras_keyboard` + handlers `handle_report_extra/save/price_only/extras_text`
- **Mini App (vanilla)**: новые поля лимита, канистр, комментарий. submitReport отправляет все поля
- **Mini App (React)**: расширен `postReport`, новый `postReview`
- `handle_web_app_data` (TG) расширен: принимает все поля + `type=review`

## Парсеры (commit 6ae4f3d, 952549a, 4735c81, 47c3504)
- `gdebenz_areas_all.py` регенерирован из БД: **4,233 города** (было 2,735)
- `parse_azslive.py`: сохраняет `queue_size=5` при статусе "queue"
- `parse_benzinmap.py` + `parse_azslive.py` добавлены в API `/api/parse`
- Результат: 533 города с live данными (было 117), 398 отчётов с лимитом/канистрами (было 45), 595 отчётов с очередями (было 0)

## 03.07.2026 — Глубокий аудит кодовой базы

### КРИТИЧНЫЕ

#### 1. callback.message.from_user = бот (не пользователь) — handlers.py
**Было:** Все callback-обработчики использовали `_tg_id(callback.message)` which возвращает ID бота, потому что `callback.message` — это сообщение бота с кнопками.
**Строки:** 416, 472, 501, 519, 617, 668, 811, 1867, 1942, 2119, 2220, 2257, 2342, 2380
**Стало:** Создана функция `_ensure_callback_user(callback)` и заменены все вызовы на `callback.from_user.id`.
**Влияние:** Все callback-обработчики работали неправильно — premium, подписки, отчёты, отзывы, владельцы АЗС.

#### 2. VK _send() без аргумента msg — vk_bot.py
**Было:** `_send(text, keyboard)` —缺少 `msg`.
**Строки:** 597-600, 621-624
**Стало:** `_send(msg, text, keyboard)`
**Влияние:** Краш при поиске по адресу и отправке отзыва в VK.

#### 3. VK "Я владелец" ловился wrong handler — vk_bot.py
**Было:** Handler на строке 897 ловил "владелец" до role-selection handler на строке 931.
**Стало:** `if "владелец" in text.lower() and uid not in _owner_waiting_role:`
**Влияние:** Flow выбора роли владельца был полностью сломан.

#### 4. VK "Отменить" ≠ "Отмена" — vk_bot.py
**Было:** Кнопки "❌ Отменить", handler проверял `"Отмена" in text`.
**Стало:** `"Отмен" in text`
**Влияние:** Кнопка отмены не работала.

#### 5. VK state undefined crash — vk_bot.py
**Было:** `state.get("awaiting_address_query")` без предварительного определения `state`.
**Стало:** `state = _user_state.get(uid, {})` перед проверкой.
**Влияние:** Краш при вводе запроса в поиске по адресу.

#### 6. DB connection leak — db.py
**Было:** `rows = await (await _db.acquire()).fetch(...)` — соединение не освобождалось.
**Стало:** `async with _db.acquire() as conn: rows = await conn.fetch(...)`
**Строка:** 1608
**Влияние:** Утечка соединений, исчерпу пула.

#### 7. datetime.now() vs tz-aware PG — db.py
**Было:** `datetime.now()` vs timezone-aware datetime от asyncpg → TypeError.
**Строки:** 465, 714
**Стало:** `datetime.now(timezone.utc)` с проверкой tzinfo.
**Влияние:** Краш в is_station_promoted и is_premium.

#### 8. api.py missing imports — api.py
**Было:** `is_premium`, `get_premium_info` не импортированы.
**Стало:** Добавлены в `from db import (...)`.
**Влияние:** /api/premium-status и premium detection в /api/stations крашились.

#### 9. api.py _db undefined — api.py
**Было:** `_db.execute()`, `_db.acquire()` в handle_import_prices — `_db` не определён в api.py.
**Стало:** Заменено на `db._fetch()`.
**Влияние:** POST /api/import_prices крашился.

#### 10. api.py double init_db — api.py
**Было:** `init_db()` вызывался и в main(), и в `_on_startup`.
**Стало:** `_on_startup` проверяет `_db is None` перед вызовом.
**Влияние:** Утечка пула соединений.

### ВЫСОКИЕ

#### 11. report_start filter — handlers.py
**Было:** `F.data.startswith("report:")` ловил `report_city:`, `report_pick:` → int() на строке → краш.
**Стало:** `F.data.regexp(r"^report:\d+$")`

#### 12. Diesel "АИ-diesel" — utils.py
**Было:** `f"АИ-{fuel}"` для всех типов топлива.
**Стало:** Проверка `fuel == "diesel"` → "Дизель".
**Строки:** 46, 294, 347

#### 13. enrich_addresses убивал DB pool — enrich_addresses.py
**Было:** `db.close_db()` вызывался при `_API_MODE` (внутри API).
**Стало:** Проверка `if not os.getenv("_API_MODE")` перед init_db/close_db.

#### 14. Address search по словам — db.py
**Было:** "Газпром Минская" искалось как одна строка → ничего не находило.
**Стало:** Запрос разбивается на слова, каждое слово ищется отдельно.

#### 15. TG FSM handler ordering — handlers.py
**Было:** `report_address_search` зарегистрирован ПОСЛЕ catch-all `handle_main_button`.
**Стало:** Зарегистрирован ПЕРЕД catch-all.

### СРЕДНИЕ

#### 16. Esc/escape_html дублирование — handlers.py
Две функции делают одно и то же. `escape_html` не используется.

#### 17. VK _user_state race condition
Словарь `_user_state` — plain dict, нет блокировок между read/write.

#### 18. VK memory leak
`_user_state`, `_owner_waiting_*`, `_cache` растут без ограничений.

#### 19. api.py CORS wildcard
`ALLOWED_ORIGINS = "*"` — любые сайты могут делать запросы.

#### 20. PG find_stations_by_name — missing params for relevance/limit — db.py
**Было:** `*params, limit` передавалось в conn.fetch(), но SQL использовал `${first_idx}` и `${limit_idx}` которые не соответствовали переданным параметрам. Для запроса "Лукойл" — SQL期待 $1 (word), $2 (relevance), $3 (limit), но передавалось только 2 значения.
**Стало:** `params.append(words[0]); first_idx=len(params); params.append(limit); limit_idx=len(params); conn.fetch(sql, *params)`.
**Влияние:** Все поисковые запросы через /api/search крашились с 500 Internal Server Error.
