# Архитектура системы

## Запуск
`main.py` запускает 4 компонента параллельно:
1. **TG бот** (aiogram polling) — handlers.py
2. **VK бот** (vk_api polling) — vk_bot.py
3. **HTTP API** (aiohttp, порт 8080) — api.py
4. **Workers** — push_loop + channel_loop

## База данных
- PostgreSQL на Supabase (prod)
- SQLite для локальной разработки
- Автоопределение через `USE_SQLITE` env var
- `_execute()` конвертирует `?` → `$1,$2` для PG

### Основные таблицы
- `stations` — АЗС (id, name, operator, city, address, lat, lon, is_verified, is_active)
- `reports` — отчёты (station_id, fuel_type, available, price, queue_size, source, user_id, created_at)
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
- `parse_gdebenz.py` — gdebenzin
- `enrich_addresses.py` — обогащение адресов через Photon/Nominatim

### Запуск парсеров
1. **Через API:** `GET /api/parse?key=benzin-parse` → asyncio.create_task
2. **Через Render cron:** `orchestrator.py --once` каждый час
3. **Через VPS crontab:** `/opt/benzin-ryadom/scripts/orchestrator.py`

### _API_MODE флаг
Когда парсеры запускаются внутри API (через /api/parse),他们 НЕ должны вызывать `init_db()`/`close_db()`. Флаг `_API_MODE=1` ставится в api.py, парсеры проверяют `os.getenv("_API_MODE")`.

## Подписки
### TG
- Требуется подписка на `@benzyn_ryadom`
- Проверка через `bot.get_chat_member()`
- Не блокирует `/start`

### VK
- Требуется подписка на сообщество `vk.com/benzyn_ryadom` (ID: 239975253)
- Проверка через VK API `groups.isMember`
- `_require_sub()` wrapper + catch-all handler

## Отчёты пользователей
- FSM: выбор города → выбор АЗС → выбор топлива → статус
- Альтернатива: поиск по адресу (после выбора города)
- Отчёты с `source='user'` живут 7 дней
- Отчёты парсеров живут 2 часа

## Отзывы
- "⭐ Оценить качество бензина" в карточке АЗС
- Выбор топлива → рейтинг 0-5 → комментарий
- Сохраняется в таблицу `reviews`

## Push-уведомления
- `push_worker.py` — проверяет новые отчёты по подпискам
- Коoldown между уведомлениями
- Premium: без cooldown

## Канал
- `channel_poster.py` — постит свежие цены в `@benzyn_ryadom`
- Посты с визуалами (созданы в `посты/визуал/`)
