# Оставшиеся проблемы и планы

## Не сделано (HIGH)

### VK/TG ID collision
- VK peer_id хранится в `telegram_id` колонке users
- Если TG user и VK user имеют одинаковый числовой ID — это один и тот же user
- Решение: добавить `vk_id` колонку + миграция существующих VK users
- Сложность: требует миграция БД, обновление upsert_user, get_user_id_by_telegram_id
- Статус: **не сделано** (требует отдельного спринта)

### Парсеры-«пустышки» с station_id=1
- `parse_quick.py`, `parse_all_available.py` сохраняют отчёты с `station_id=1` (placeholder)
- Данные не привязаны к реальным АЗС
- Нужно: либо починить маппинг, либо удалить эти парсеры

### Моковые парсеры
- `parse_official_networks.py` — все URL выдуманные
- `parse_fuel_quality.py` — данные из хардкод-таблиц
- `parse_queue_data.py` — нет реальных источников
- `parse_fuel_limits.py` — данные из хардкод-таблиц

### Очереди только из azslive
- Реальные очереди — только из TG-каналов (заблокированы антиспамом)
- azslive даёт только "queue" статус (5 машин hard-coded)
- Нужны новые источники: Яндекс.Пробки, OOH-камеры, gdebenz user reports

## Сделано в этой сессии (12.07.2026)

✅ 13 багов исправлено (CRITICAL/HIGH)
✅ Unified reporting в TG/VK/MiniApp — все поля (price, limit, canister, queue, comment)
✅ `/api/reviews` endpoint добавлен
✅ gdebenz_areas_all.py регенерирован: 4,233 города (было 2,735)
✅ GitHub Actions cron — замена Render cron
✅ Покрытие данными: 117 → 533 городов (+356%)
✅ Live отчёты: 18,105 → 24,978 (+38%)
✅ Лимит/канистры: 45 → 398 (+783%)
✅ Очереди: 0 → 595 (new)

## Оставшиеся баги (из аудита 03.07)

### СРЕДНИЕ
1. **VK _user_state race condition** — dict без блокировок между async read/write
2. **VK memory leak** — `_user_state`, `_owner_waiting_*`, `_cache` растут без TTL
3. **api.py CORS** — `ALLOWED_ORIGINS` есть, но нужно ограничить в проде
4. **handlers.py bot-to-bot loop** — `F.from_user.is_bot` ловит любого бота
5. **db.py sync sqlite3** — `_import_from_sqlite_pg` блокирует event loop
6. **push_worker N+1** — `is_premium()` вызывается per subscriber
7. **channel_poster created_at_timestamp** — несуществующий ключ, сортировка случайная
8. **api.py handle_logs** — весь лог в память, может OOM
9. **utils.py format_time_ago** — возвращает raw строку при ошибке парсинга

### НИЗКИЕ
10. **db.py import re** — на каждый вызов _fetch/_execute
11. **db.py redundant imports** — datetime импортируется локально
12. **keyboards.py unused city param** — в report_station_keyboard
13. **messages.py missing space** — "—выбери" → "— выбери"
14. **config.py type hints** — `list = None` → `list | None = None`

## Планы по развитию

### Парсеры
- Добавить реальный источник очередей (Яндекс.Пробки, OOH камеры)
- Починить `parse_quick.py` и `parse_all_available.py` (station_id=1 bug)
- Добавить парсер для новых регионов (Запорожская, Херсонская обл.)
- Включить Playwright-парсеры (benzin-price.ru headless, Yandex.Карты POI)

### Бот
- Унификация VK и TG Mini App (сейчас две версии)
- VK Mini App (только TG Mini App сейчас)
- Улучшить push-уведомления
- Premium-функции: без рекламы, push без задержек

### Монетизация
- Партнёрские программы (ОСАГО, автозапчасти)
- CPA-сети (Admitad, CityAds)
- Пока рано — мало пользователей

### Раскрутка
- 7-дневный контент-план создан в папке `посты/`
- Визуалы: 8 статичных (1080×1080) + 5 анимированных шортсов
- SEO: BotFather описание, VK community SEO
- Рекламный пост с причинами выбора
