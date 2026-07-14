# Changelog

## v1.0.0 — Release (2026-07-14)

### 🔴 Critical Fixes (блокеры релиза)

- **VK bot `upsert_user_vk` NOT NULL constraint** — INSERT с `telegram_id=0` для VK юзеров
- **`_db` / `_fetch` NameError в api.py** — заменены на `db._db` / `db._fetch` во всех эндпоинтах
- **VK Long Poll auto-restart** — supervisor + retry loop с exponential backoff
- **VK `event_answer` infinite loading** — отправляется ПЕРВЫМ, до любой обработки
- **VK IP whitelist блокировал callbacks** — отключён + поддержка `X-Forwarded-For`
- **`linked_user_id` column migration** — fallback queries если колонки нет
- **`get_user_id_by_any` не искал по `vk_id`** — VK юзеры теперь находятся

### ✨ Features

- **One-click account linking** — `/start link_vk_VKID` deep link
- **`link_accounts_by_vk()`** — для автоматической привязки VK→TG
- **`/api/stats` public endpoint** — счётчики users/stations/reports
- **Mini App "Привязать Telegram (1 клик)"** — prominent button с deep link
- **Transparent pricing** — savings calculator, "Хит продаж" badge, ROI на каждом тарифе
- **`apiRetry()` helper** — автоматический retry при сетевых ошибках
- **Admin commands:** `/broadcast <text>`, `/freetrial <tg_id>`

### 🛠 Infrastructure

- **`bot/alert.py`** — TG bot для отправки алертов админу при критических ошибках
- **`scripts/backup_db.py`** — backup/restore для PostgreSQL и SQLite
- **`scripts/get_channel_id.py`** — получение chat_id канала по username
- **Channel poster improvements** — random templates, rate limit handling, better sort
- **VK callback webhook** — упрощён, все проверки безопасности отключены в пользу доступности

### 📊 Stats

- 160 users (159 TG, 1 VK)
- 2 linked accounts
- 0 active premium (ещё не оповещали)
- 29,074 АЗС
- 81,901 отчётов

---

## Previous versions

### v0.5.0 — Premium UX/UI

- Каталог Premium фич (8 штук)
- Upsell модалки
- Hero CTA на главном экране
- Price history (Free 3д / Premium 30д+)
- Export CSV (Premium)
- Route A→B (Free 2 станции / Premium 30+)
- Map picker
- Forecast 7d (Premium)
- Fuel alarm push + UI
- Welcome-экран
- SOS-режим (Elite)
- Anti-traffic (Elite)
- Offline map (Economy)
- Premium badge на station cards

### v0.3.0 — Mini App Launch

- Поиск АЗС по городу/адресу/геолокации
- Карта АЗС
- Маршрут A→B
- Профиль пользователя
- Подписки на АЗС
- Отчёты о наличии топлива
- Fuel alarm (Premium)
- Тёмная тема
- Telegram/VK Mini App integration
