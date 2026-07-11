# Архитектура системы

## Запуск
`main.py` запускает 4 компонента параллельно:
1. **TG бот** (aiogram polling) — handlers.py
2. **VK бот** (vkbottle polling) — vk_bot.py
3. **HTTP API** (aiohttp, порт 8080) — api.py
4. **Workers** — push_loop + channel_loop

## База данных
- PostgreSQL на Supabase (prod)
- SQLite для локальной разработки
- Автоопределение через `USE_SQLITE` env var
- `_execute()` конвертирует `?` → `$1,$2` для PG

### Основные таблицы
- `stations` — АЗС (id, name, operator, city, address, lat, lon, is_verified, is_active)
- `reports` — отчёты (station_id, fuel_type, available, price, queue_size, source, user_id, created_at, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment, expires_at)
- `users` — пользователи (telegram_id, username, first_name, total_reports, badges)
- `subscriptions` — подписки на АЗС (user_id, station_id, radius_km, fuel_types)
- `reviews` — отзывы о качестве (station_id, user_id, fuel_type, rating 0-5, comment)
- `owner_stations` — владельцы АЗС
- `premium` — Premium-подписки

### Важное представление
```sql
CREATE VIEW station_current_status AS
SELECT DISTINCT ON (station_id, fuel_type)
  station_id, fuel_type, available, price, queue_size, has_limit,
  confidence, created_at, source
FROM reports
WHERE
  -- User reports: 7 дней
  (source IN ('user','telegram','vk','miniapp','owner') AND created_at > NOW() - INTERVAL '7 days')
  OR
  -- Parser reports: 2 часа
  (source NOT IN ('user','telegram','vk','miniapp','owner') AND created_at > NOW() - INTERVAL '2 hours')
ORDER BY station_id, fuel_type,
  CASE WHEN source IN ('user','telegram','vk','miniapp','owner') THEN 0 ELSE 1 END,
  confidence DESC, created_at DESC;
```

## Парсеры
- `parse_fuelprice.py` — fuelprice.ru (18K+/час)
- `parse_tg_channels.py` — TG каналы (3-5K/час, через session string)
- `parse_ishubenzin.py` — ishubenzin (100-250/час)
- `parse_gdebenz.py` — gdebenzin (30K+ исторических)
- `parse_benzinmap.py` — benzinmap.ru (62 региона, лимиты/канистры)
- `parse_azslive.py` — azslive.ru (26K АЗС, наличие)
- `parse_benzin_status_tech.py` — benzin-status.tech Mini App API
- `parse_vk_groups.py` — 94 VK группы (наличие, цены)
- `parse_networks.py` — сети АЗС (Лукойл/Газпромнефть/Роснефть/Татнефть/Башнефть)
- `parse_yandex_fuel.py` — Яндекс.Заправки
- `enrich_addresses.py` — обогащение адресов через Photon/Nominatim

### Запуск парсеров
1. **Через API:** `GET /api/parse?key=benzin-parse` → asyncio.create_task
2. **Через GitHub Actions cron:** каждый час → пингует API endpoint
3. **Локально:** `python3 scripts/parse_xxx.py`

### API_MODE флаг
Когда парсеры запускаются внутри API (через /api/parse), они НЕ должны закрывать общий пул.
- `db.API_MODE = True` ставится в api.py перед запуском
- `db.init_db()` — идемпотентна (если _db уже инициализирован, не пересоздаёт)
- `db.close_db()` — если `API_MODE=True`, не закрывает пул
- Парсеры могут безопасно вызывать `await db.init_db()` и `await db.close_db()` — это no-op в API контексте

## Подписки
### TG
- Требуется подписка на `@benzyn_ryadom`
- Проверка через `bot.get_chat_member()`
- Не блокирует `/start`

### VK
- Требуется подписка на сообщество `vk.com/benzyn_ryadom` (ID: 239975253)
- Проверка через VK API `groups.isMember`
- `_require_sub()` wrapper + catch-all handler

## Unified Reporting (12.07.2026)
Все клиенты (TG бот, VK бот, Mini App) пишут в **одну таблицу** `reports` через единый API `POST /api/reports`.

### TG бот — Flow
1. Главное меню → "Сообщить"
2. Выбор города → Выбор АЗС → Выбор топлива
3. Выбор статуса: ✅ Есть / 🕐 Очередь / ⚠️ Кончается / ❌ Нет / 💰 Только цена
4. Экран `report_extras_keyboard` (опционально):
   - 💰 Указать цену → текст → сохраняется в state
   - 🚫 Указать лимит → текст → сохраняется
   - ❌ Канистры запрещены → флаг
   - 🚗 Уточнить очередь → текст → сохраняется
5. ✅ Готово (сохранить) → `add_report()` со всеми полями
6. `ReportExtrasStates` FSM хранит данные между шагами

### VK бот — Flow
1. Текст "Сообщить" → `handle_report_start`
2. Текст "город" → поиск станции → выбор
3. `handle_report_fuel` → выбор топлива
4. `handle_report_status` → выбор статуса → сохраняет в `_state[peer_id]`
5. `vk_report_extras_keyboard` (опционально):
   - 💰 Указать цену → `awaiting=price` → текст → `handle_report_extras_text`
   - 🚫 Указать лимит → `awaiting=limit` → текст
   - ❌ Канистры запрещены → сразу флаг
   - 🚗 Уточнить очередь → `awaiting=queue` → текст
6. ✅ Готово (сохранить) → `handle_report_save` → `add_report()`

### Mini App (vanilla) — Flow
1. `openReportSheet(stationId)` → UI с полями
2. Поля: fuel chips, availability (yes/queue/no), price input, queue input
3. Чекбокс "Есть лимит" → раскрывает 4 поля (литры, за раз, в день, в неделю)
4. Чекбокс "Канистры запрещены"
5. Поле "Комментарий"
6. `submitReport()` → `POST /api/reports` со всеми полями

### Mini App (React) — `postReport()`
- `WebApp.sendData()` → бот `handle_web_app_data` принимает расширенный payload
- Fallback: `POST /api/reports` напрямую

### Web App Data handler (TG)
- `handle_web_app_data` принимает:
  - `type=report` → все поля (station_id, fuel_type, available, price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment)
  - `type=review` → station_id, fuel_type, rating, comment

## Отзывы
- "⭐ Оценить качество бензина" в карточке АЗС
- TG: выбор топлива → рейтинг 0-5 → комментарий
- VK: выбор топлива → рейтинг 1-5 (нет "0 звёзд")
- Mini App: реально работает через `POST /api/reviews`
- Сохраняется в таблицу `reviews`

## Push-уведомления
- `push_worker.py` — проверяет новые отчёты по подпискам
- Коoldown между уведомлениями
- Premium: без cooldown

## Канал
- `channel_poster.py` — постит свежие цены в `@benzyn_ryadom`
- Посты с визуалами (созданы в `посты/визуал/`)
