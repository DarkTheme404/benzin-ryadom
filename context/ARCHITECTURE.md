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

### Реферальные таблицы (обновлено 20.07.2026)
- `referral_tiers` — тиры рефереров (user_id, tier, active_referrals)
  - tier: basic / ambassador / top_ref / legend
  - active_referrals: количество активных (купивших Premium) рефералов
- `referral_top3` — топ-3 рефереров по месяцам (month, user_id, rank, referral_count)
  - month: формат "2026-07"
  - rank: 1, 2, 3
- `referral_earnings` — заработок рефереров
  - level: 1, 2, 3 (уровень в цепочке)
- `referral_notifications` — очередь уведомлений (id, user_id, telegram_id, vk_id, type, message, is_sent)

### Важное представление
```sql
CREATE VIEW station_current_status AS
SELECT DISTINCT ON (station_id, fuel_type)
  station_id, fuel_type, available, price, queue_size, has_limit,
  confidence, created_at, source
FROM reports
WHERE
  (source IN ('user','telegram','vk','miniapp','owner') AND created_at > NOW() - INTERVAL '7 days')
  OR
  (source NOT IN ('user','telegram','vk','miniapp','owner') AND created_at > NOW() - INTERVAL '2 hours')
ORDER BY station_id, fuel_type,
  CASE WHEN source IN ('user','telegram','vk','miniapp','owner') THEN 0 ELSE 1 END,
  confidence DESC, created_at DESC;
```

## Реферальная система — Архитектура (20.07.2026)

### Тиры и комиссии
```
basic (0-49 активных) → 50% с 1-го уровня
ambassador (50-99) → 55%
top_ref (100-199) → 60%
legend (200+) → 65%
топ-3 месяца → 70% (вместо тира)
2-й уровень → 5% для всех Elite/Founder
3-й уровень → 3% только для топ-3
```

### Flow комиссий
1. Пользователь покупает Premium → `record_referral_commission()`
2. Определяем реферера → проверяем тир
3. 1-й уровень: `get_commission_rate(tier)` × сумма платежа
4. 2-й уровень: 5% от суммы платежа → рефереру реферера
5. 3-й уровень: 3% от суммы платежа → рефереру реферера реферера (только для топ-3)
6. Обновляем `referral_earnings` (level=1,2,3) и баланс
7. Уведомляем реферера

### Ежемесячный cron (1-е число)
1. `update_all_tiers()` — пересчёт тиров для всех
2. `calculate_top3(month)` — расчёт топ-3 за прошлый месяц
3. Уведомления: топ-3 + смена тира

### Антифрод
- `check_self_referral()` — проверяет что TG ID + VK ID не совпадают с реферальным кодом

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

### VK бот — Flow
1. Текст "Сообщить" → `handle_report_start`
2. Текст "город" → поиск станции → выбор
3. `handle_report_fuel` → выбор топлива
4. `handle_report_status` → выбор статуса → сохраняет в `_state[peer_id]`
5. `vk_report_extras_keyboard` (опционально)
6. ✅ Готово (сохранить) → `handle_report_save` → `add_report()`

### Mini App (vanilla) — Flow
1. `openReportSheet(stationId)` → UI с полями
2. Поля: fuel chips, availability (yes/queue/no), price input, queue input
3. Чекбокс "Есть лимит" → раскрывает 4 поля
4. Чекбокс "Канистры запрещены"
5. Поле "Комментарий"
6. `submitReport()` → `POST /api/reports` со всеми полями

## Push-уведомления
- `push_worker.py` — проверяет новые отчёты по подпискам
- Коoldown между уведомлениями
- Premium: без cooldown
- Реферальные уведомления: комиссии, вывод средств, топ-3, смена тира

## Канал
- `channel_poster.py` — постит свежие цены в `@benzyn_ryadom`
- Посты с визуалами (созданы в `посты/визуал/`)
