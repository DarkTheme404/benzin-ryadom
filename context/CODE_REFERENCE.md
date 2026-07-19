# Быстрый справочник по коду

## handlers.py (TG бот)

### Команды
- `cmd_start` — /start, главное меню
- `cmd_find` — /find, поиск АЗС
- `cmd_subscribe` — /subscribe, подписки
- `cmd_register_owner` — /register_owner, регистрация владельца
- `cmd_my_stations` — /my_stations, мои АЗС
- `cmd_profile` — /profile, профиль
- `cmd_help` — /help
- `cmd_set_ad` — /set_ad, баннер (формат: `Текст | URL` или `off`)

### FSM-машины
- `SubscribeStates` — подписки (waiting_geo → waiting_radius)
- `ReportAddressStates` — поиск по адресу (waiting_query)
- `ReviewStates` — отзывы (waiting_comment)
- `ReportExtrasStates` — **расширенные отчёты (price/limit/queue)** (waiting_price/waiting_limit/waiting_queue)
- `BugReportStates` — баг-репорт
- `IdeaStates` — идеи

### Callback-обработчики
- `report_start` — начало отчёта (filter: `^report:\d+$`)
- `report_fuel` — выбор топлива
- `report_submit` — выбор статуса (yes/queue/low/no)
- `report_extra_callback` — **выбор экстра-поля (price/limit/canister/queue)**
- `report_save_with_extras` — **сохранение со всеми собранными данными**
- `report_price_callback` — **быстрый отчёт "только цена"**
- `report_city_callback` — выбор города для отчёта
- `report_address_start` — поиск по адресу
- `review_start` — начало отзыва
- `review_pick_fuel` — выбор топлива для отзыва
- `review_submit` — отправка отзыва (rating 0-5)
- `subscribe_station` — подписка на АЗС
- `show_station_details` — карточка АЗС (`st:`)
- `go_home_callback` — "В начало"
- `premium_trial_callback` — пробный Premium
- `buy_premium_callback` — покупка Premium
- `handle_web_app_data` — **принимает расширенный payload от Mini App (type=report или type=review)**

### Message-handlers
- `handle_report_extras_input` — **обработка текстового ввода цены/лимита/очереди**
- `handle_main_button` — кнопки главного меню
- `handle_location` — геолокация
- `handle_text_search` — поиск по тексту

### Вспомогательные
- `_tg_id(obj)` — ID пользователя из Message или CallbackQuery
- `_ensure_callback_user(callback)` — создание/получение пользователя из callback
- `_require_subscription(message)` — проверка подписки TG
- `_require_subscription_callback(callback)` — проверка подписки через callback
- `_get_main_status_icon(statuses)` — иконка станции (✅/⚠️/❌/❓)

## vk_callback.py (VK бот — callback API)

### Handlers (в `process_message_event`)
- `handle_report_start(peer_id)` — начало отчёта
- `handle_report_fuel(peer_id, station_id, fuel)` — выбор топлива
- `handle_report_status(peer_id, station_id, fuel, value)` — выбор статуса → переход в extras
- `handle_report_extra(peer_id, station_id, fuel, status, extra_type)` — **выбор экстра-поля (price/limit/canister/queue)**
- `handle_report_save(peer_id, station_id, fuel, status)` — **сохранение со всеми полями**
- `handle_report_price_only(peer_id, station_id, fuel)` — **быстрый отчёт "только цена"**
- `handle_report_extras_text(peer_id, text)` — **обработка текстового ввода цены/лимита/очереди**
- `handle_review_start(peer_id, station_id)` — начало отзыва
- `handle_review_fuel(peer_id, station_id, fuel)` — выбор топлива для отзыва
- `handle_review_rating(peer_id, station_id, fuel, rating)` — выбор рейтинга 1-5
- `handle_subscribe_station(peer_id, station_id)` — подписка на АЗС
- `handle_geo(peer_id, geo)` — геолокация
- `handle_text_search(peer_id, text)` — поиск

### Вспомогательные
- `_get_user_id(peer_id)` — внутренний user_id из peer_id
- `_ensure_user(peer_id, first_name)` — создание/обновление пользователя
- `_vk_subscribe_keyboard()` — клавиатура проверки подписки
- `_vk_send(peer_id, text, keyboard)` — отправка сообщения

## vk_keyboards.py

### Клавиатуры
- `vk_main_menu()` — главное меню
- `vk_city_keyboard(cities)` — выбор города
- `vk_fuel_filter_keyboard()` — фильтр топлива
- `vk_fuel_type_keyboard(station_id)` — выбор топлива для отчёта
- `vk_report_status_keyboard(station_id, fuel)` — статус наличия (yes/queue/low/no) + **💰 Только цена**
- `vk_report_extras_keyboard(station_id, fuel, status)` — **экран доп. полей (price/limit/canister/queue)**
- `vk_review_fuel_keyboard(station_id)` — топливо для отзыва
- `vk_review_rating_keyboard(station_id, fuel)` — рейтинг 1-5
- `vk_subscribe_geo_keyboard()` — отправка геолокации
- `vk_subscribe_radius_keyboard()` — выбор радиуса подписки
- `vk_station_actions(station_id)` — действия с АЗС
- `vk_premium_keyboard()` — Premium
- `vk_donate_keyboard()` — донат
- `vk_price_filter_keyboard()` — фильтр по цене
- `vk_network_filter_keyboard()` — фильтр по сети
- `_callback_button`, `_button`, `_link_button` — builders

## keyboards.py (TG)

### Клавиатуры
- `main_menu_keyboard()` — главное меню
- `main_inline_keyboard()` — inline кнопки
- `report_status_keyboard(station_id, fuel)` — статус наличия (yes/queue/low/no) + **💰 Только цена**
- `report_extras_keyboard(station_id, fuel, status)` — **экран доп. полей (price/limit/canister/queue)**
- `report_station_keyboard(stations)` — список АЗС
- `report_city_keyboard(cities)` — выбор города
- `report_address_results_keyboard(stations)` — результаты поиска по адресу
- `review_rating_keyboard(station_id, fuel)` — рейтинг 0-5
- `review_fuel_keyboard(station_id)` — топливо для отзыва
- `fuel_type_keyboard(station_id)` — выбор топлива
- `city_keyboard(cities)` — выбор города
- `filters_keyboard(city)` — фильтры
- `price_filter_keyboard(city)` — фильтр по цене
- `network_filter_keyboard(city)` — фильтр по сети
- `premium_keyboard()` — Premium
- `web_app_keyboard(web_app_url)` — открыть Mini App
- `bug_report_keyboard()` — баг-репорт
- `idea_keyboard()` — идеи

## db.py

### Основные функции
- `init_db()` / `close_db()` — инициализация/закрытие БД (idempotent + API_MODE safe)
- `upsert_user()` — создание/обновление пользователя
- `get_or_create_user(message)` — из Message (TG или VK)
- `add_report()` — **добавление отчёта (price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment)**
- `add_review()` — отзыв (rating 0-5, comment)
- `find_nearest_stations()` — ближайшие АЗС по координатам
- `find_stations_by_city()` — АЗС по городу
- `find_stations_by_name()` — поиск по названию
- `find_stations_by_address()` — поиск по адресу (с разбиением на слова)
- `get_station_current_status()` — текущий статус АЗС
- `stale_old_reports(source)` — удаление старых отчётов
- `get_source_priority(source)` — приоритет источника для confidence
- `calculate_confidence(source, age_hours, agreement_count, base_confidence)` — расчёт confidence
- `get_recency_bonus(age_hours)` — бонус за свежесть

### Confidence модель
- `SOURCE_PRIORITY` — словарь приоритетов источников
- `RECENCY_BONUS` — список (max_hours, bonus)

### Premium
- `activate_premium()` — активация Premium
- `is_premium()` — проверка Premium
- `get_premium_info()` — информация о Premium

### Подписки
- `add_subscription()` — добавление подписки
- `get_user_subscriptions()` — подписки пользователя

### Владельцы
- `add_owner_station()` — регистрация владельца
- `is_owner_of_station()` — проверка владельца
- `set_owner_station_verified()` — подтверждение
- `get_owner_stations(user_id)` — АЗС владельца
- `get_station_analytics(station_id)` — аналитика (просмотры, отчёты, подписчики)

### Аналитика
- `get_station_analytics(station_id, days=30)` — для владельцев
- `check_and_award_badges(user_id)` — бейджи

## api.py

### Эндпоинты
- `GET /api/health` — здоровье
- `GET /api/admin/stats` — статистика
- `GET /api/search?q=` — поиск
- `GET /api/stations?lat=&lon=&fuel=&max_price=&network=` — ближайшие
- `GET /api/stations/by-city?city=&fuel=` — по городу
- `GET /api/stations/emergency?city=&fuel=` — экстренный
- `GET /api/stations/{id}` — детали АЗС
- `GET /api/stations/{id}/prices` — цены по источникам
- `GET /api/price-history/{id}` — история цен
- `GET /api/station-prices/{id}` — текущие цены
- `GET /api/station-analytics/{id}` — аналитика для владельца
- `GET /api/premium-status` — статус Premium
- `POST /api/reports` — **создание отчёта** (price, queue_size, has_limit, limit_liters, limit_per_visit, limit_daily, limit_weekly, canister_ban, comment, fuel_type)
- `POST /api/reviews` — **отзыв** (rating 0-5, comment, fuel_type)
- `POST /api/price-update` — обновление цены
- `POST /api/import_prices` — импорт от внешних парсеров
- `POST/GET /api/parse?key=` — запуск парсеров (benzinmap + azslive + 4 других)
- `GET /api/parse-benzin` — benzin-status.tech парсер
- `POST /api/vk/callback` — VK webhook
- `POST /api/vk/test-event` — тест VK событий
- `GET /api/enrich` — обогащение адресов
- `GET /api/import-osm` — OSM импорт
- `GET /api/logs` — последние логи
- `GET /api/reverse-geocode?lat=&lon=` — геокодирование

### Rate limits
- `RATE_LIMIT_GET = 30` — для GET
- `RATE_LIMIT_POST = 10` — для POST
- `RATE_LIMIT_ADMIN = 5` — для admin endpoints

## utils.py

### Форматирование
- `format_distance(km)` — расстояние
- `format_fuel_status(status)` — статус топлива
- `format_delivery_time(dt)` — время доставки
- `format_time_ago(dt)` — "5 мин назад"
- `format_station_card(station, statuses)` — карточка АЗС
- `get_main_status(station)` — **агрегированный статус станции (icon + price)**
- `format_for_vk(text)` — конвертация HTML → VK

## Flutter App (flutter-app/)

### Точка входа
- `lib/main.dart` — SplashScreen → RegistrationScreen/GuestMode → MainScreen

### Авторизация
- `lib/screens/registration_screen.dart` — _register(), _login(), _skip()
- Регистрация: name + password + vk_link + tg_link → POST /api/user/register
- Вход: name + password → POST /api/user/login
- **Критично**: Flutter хранит `telegramId` (не `userId`) для API запросов
- `StorageService.userId` = internal id, `StorageService.telegramId` = для API
- `ApiService.setUserId(telegramId)` — устанавливает `telegram_id` параметр

### API Service
- `lib/services/api_service.dart` — 40+ эндпоинтов
- `_get()` / `_post()` — с `telegram_id` параметром автоматически
- `registerUser(body)` — longTimeout (60s)
- `loginUser(name, password)` — longTimeout (60s)
- `getStations(lat, lon, fuel)` — GET /api/stations
- `getStationsByCity(city, fuel)` — GET /api/stations/by-city
- `searchStations(q)` — GET /api/search
- `getUserProfile()` — GET /api/user/profile

### Модель станции
- `lib/models/station.dart` — `Station.fromJson()` парсит `statuses[]` массив
- `fuelStatusForType(fuelType)` — вычисляет статус наличия (available/partial/unavailable)
- Не использует `prices{}` или `availability{}` — бэкенд отдаёт `statuses[]`

### Карта
- `lib/screens/map_screen.dart` — flutter_map + OpenStreetMap
- `_loadStations(lat, lon)` — загружает станции при движении карты
- `_onMapEvent()` — debounce 800ms
- Маркеры: зелёный (available), жёлтый (partial), красный (unavailable), серый (unknown)

### Бэкенд: регистрация
- `bot/api.py:handle_user_register` — POST /api/user/register
  - body: {name, password, device_id, vk_link?, tg_link?}
  - Хеширует пароль через hashlib.pbkdf2_hmac (не bcrypt!)
  - VK lookup: сначала по numeric vk_id, потом по screen_name
  - Возвращает: {ok, user_id, telegram_id, account_type, name, ...}
- `bot/api.py:handle_user_login` — POST /api/user/login
  - body: {name, password}
  - Ищет по имени (case-insensitive), проверяет хеш
  - Возвращает: {ok, user_id, telegram_id, account_type, name, premium, ...}

### Бэкенд: пароль
- Колонка `users.password_hash` — формат: `{hash_hex}:{salt_hex}`
- Хеш: hashlib.pbkdf2_hmac('sha256', password, salt, 100000)
- Проверка: split(':') → rebuild hash → compare hex

### Бэкенд: рефералы (Elite gate)
- `bot/db.py:record_referral_commission()` — проверяет `referrer_tier in (elite,) or is_founder()`
- Только Elite/Founder рефереры получают 50% комиссии

