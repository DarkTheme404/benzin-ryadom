# Оставшиеся проблемы и планы

## Сделано (19.07.2026 — Flutter авторизация)

✅ Пароль при регистрации/входе (hashlib pbkdf2_hmac, без bcrypt)
✅ Бэкенд: POST /api/user/register (password, vk_link, tg_link)
✅ Бэкенд: POST /api/user/login (name, password)
✅ Бэкенд: возврат telegram_id из register/login
✅ Бэкенд: VK lookup по screen_name (не только числовой vk_id)
✅ Бэкенд: password_hash колонка в users (SQLite + PostgreSQL миграция)
✅ Flutter: поля пароля (регистрация + вход)
✅ Flutter: переключение Регистрация/Вход
✅ Flutter: сохранение telegramId (не userId) для API запросов
✅ Flutter: таймаут 60 сек для регистрации (Render cold start)
✅ Flutter: кнопка «Выйти» в профиле (с подтверждением)
✅ Flutter: ошибка API показывает детали (status code, body)
✅ Реферальная комиссия — только для Elite+ рефереров (backend)
✅ Реферальная карточка — показывает ограничение для non-Elite (Flutter + MiniApp)

## Сделано ранее (14-17.07.2026)

✅ Premium: 3 тарифа + Founder Pack
✅ Premium UX: lock-иконки, gold-градиент, hero CTA, upsell
✅ Привязка аккаунтов TG ↔ VK ↔ MiniApp (6-значные коды)
✅ Map Picker для A→B маршрутов
✅ 39 трасс с АЗС
✅ Парсеры: 12 источников, 28K+ станций
✅ Standalone Android APK (Capacitor)
✅ Flutter: сплеш, регистрация, карта, поиск, профиль, премиум

## HIGH — Нужно сделать

### Авторизация: привязка к существующему VK/TG аккаунту
- Backend ищет по `screen_name` VK → находит существующий VK-аккаунт с премиумом
- **Статус**: backend готов, нужен тест на реальных данных
- **Проверка**: зарегистрироваться с VK ссылкой `vk.com/<username>`, проверить что премиум появился
- **Проблема**: если VK username не совпадает с `screen_name` в БД → создаётся новый аккаунт

### Flutter: реальные данные на карте
- Карта загружает АЗС через `/api/stations?lat=&lon=`
- **Проблема**: не все станции имеют координаты; радиус 30/100 км
- **Нужно**: проверить что маркеры отображаются в伊万овской области

### Flutter: поиск и данные
- `/api/stations/by-city` для поиска по городу
- Цены и наличие из `statuses[]` массива (парсинг готов)
- **Нужно**: протестировать поиск в реальных городах

## MEDIUM

### VK/TG ID collision (legacy)
- VK peer_id хранится в `telegram_id` колонке
- Требует миграции: добавить `vk_id` колонку
- **Статус**: не сделано

### Push-уведомления для Flutter
- Нет push-интеграции
- Нужен Firebase Cloud Messaging или аналог
- **Приоритет**: низкий (основные каналы — TG/VK боты)

### Оффлайн-режим
- Кеширование станций в SQLite
- Кеширование map tiles
- **Приоритет**: низкий

## НИЗКИЕ

1. **TelegramConflictError** при деплое — две инстанции (30-60 сек)
2. **Render Free tier** — иногда не рестартит без ручного Clear build cache
3. **VK group token** — `groups.search` не работает (error 27)
4. **Parse-пустышки** — station_id=1 в parse_quick, parse_all_available
5. **Моковые парсеры** — parse_official_networks, parse_fuel_quality, parse_queue_data

## Планы по развитию

### Мобильное приложение (Flutter)
- Push-уведомления (FCM)
- Оффлайн-карта (кеш tiles)
- Геозоны (уведомления при приближении к АЗС)
- Камера (фото АЗС для отчётов)
- Apple Watch / WearOS виджет

### Монетизация
- Партнёрские программы (ОСАГО, автозапчасти)
- CPA-сети (Admitad, CityAds)
- Реклама в приложении (Free тариф)

### Раскрутка
- 7-дневный контент-план в `посты/`
- Визуалы: 8 статичных + 5 анимированных шортсов
- SEO: BotFather описание, VK community
