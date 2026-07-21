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
- `cmd_set_ad` — /set_ad, баннер
- `cmd_premium` — /premium, показать тарифы
- `cmd_trial` — /trial, пробный период
- `cmd_referral` — /referral, реферальная программа с тирами
- `cmd_leaderboard` — /leaderboard, топ рефереров
- `cmd_alarm` — /alarm, будильник наличия
- `cmd_sos` — /sos, экстренная помощь (Elite)
- `cmd_anti_traffic` — /anti-traffic, анти-пробка (Elite)
- `cmd_broadcast` — /broadcast, отправка в канал
- `cmd_link` — /link, привязка аккаунтов

### Реферальные callback handlers (20.07.2026)
- `ref_selling_texts_callback` — продающие тексты для копирования
- `ref_training_callback` — обучающий блок "Как заработать"
- `leaderboard_callback` — топ рефереров (callback)

### FSM-машины
- `SubscribeStates` — подписки (waiting_geo → waiting_radius)
- `ReportAddressStates` — поиск по адресу (waiting_query)
- `ReviewStates` — отзывы (waiting_comment)
- `ReportExtrasStates` — расширенные отчёты
- `BugReportStates` — баг-репорт
- `IdeaStates` — идеи
- `AntiTrafficStates` — анти-пробка (waiting_from, waiting_to)
- `SOSStates` — SOS (waiting_location)

### Callback-обработчики
- `report_start` — начало отчёта (filter: `^report:\d+$`)
- `report_fuel` — выбор топлива
- `report_submit` — выбор статуса
- `report_extra_callback` — выбор экстра-поля
- `report_save_with_extras` — сохранение со всеми данными
- `report_price_callback` — быстрый отчёт "только цена"
- `premium_trial_callback` — пробный Premium
- `buy_premium_callback` — покупка Premium
- `buy_tier_callback` — покупка тарифа (buy_economy/buy_standard/buy_elite/buy_founder)
- `check_payment_callback` — проверка оплаты
- `ref_selling_texts_callback` — продающие тексты
- `ref_training_callback` — обучение
- `leaderboard_callback` — лидерборд
- `handle_web_app_data` — принимает расширенный payload от Mini App

### Message-handlers
- `handle_main_button` — кнопки главного меню (включая SOS/Anti-traffic/Alarm)
- `handle_location` — геолокация
- `anti_traffic_from_handler` / `anti_traffic_to_handler` — ввод точек маршрута
- `sos_location_handler` — получение геолокации для SOS

### Вспомогательные
- `_tg_id(obj)` — ID пользователя из Message или CallbackQuery
- `_ensure_callback_user(callback)` — создание/получение пользователя из callback
- `_require_subscription(message)` — проверка подписки TG
- `has_feature(tier, feature)` — проверка доступности фичи по тиру Premium

## vk_callback.py (VK бот — callback API)

### Handlers (в `process_message_event`)
- `handle_report_start(peer_id)` — начало отчёта
- `handle_report_fuel(peer_id, station_id, fuel)` — выбор топлива
- `handle_report_status(peer_id, station_id, fuel, value)` — выбор статуса
- `handle_report_extra(peer_id, station_id, fuel, status, extra_type)` — выбор экстра-поля
- `handle_report_save(peer_id, station_id, fuel, status)` — сохранение
- `handle_report_price_only(peer_id, station_id, fuel)` — быстрый отчёт
- `handle_report_extras_text(peer_id, text)` — обработка текстового ввода
- `handle_review_start(peer_id, station_id)` — начало отзыва
- `handle_review_fuel(peer_id, station_id, fuel)` — выбор топлива
- `handle_review_rating(peer_id, station_id, fuel, rating)` — выбор рейтинга
- `handle_subscribe_station(peer_id, station_id)` — подписка на АЗС
- `handle_geo(peer_id, geo)` — геолокация
- `handle_text_search(peer_id, text)` — поиск
- `handle_referral(peer_id, text)` — реферальная программа с тирами
- `handle_leaderboard(peer_id)` — топ рефереров
- `handle_selling_texts(peer_id)` — продающие тексты
- `handle_training(peer_id)` — обучающий блок
- `handle_premium(peer_id)` — показать тарифы
- `handle_sos(peer_id)` — экстренная помощь (Elite)
- `handle_anti_traffic_start(peer_id)` — анти-пробка (Elite)
- `handle_alarm(peer_id)` — будильник

### Текстовые команды (20.07.2026)
- `/selling`, `selling`, `продающие`, `тексты` → `handle_selling_texts()`
- `/training`, `training`, `как заработать`, `обучение` → `handle_training()`

### Callback actions
- `action == "referral"` → `handle_referral()`
- `action == "leaderboard"` → `handle_leaderboard()`
- `action == "sos"` → `handle_sos()`
- `action == "anti_traffic"` → `handle_anti_traffic_start()`

### Вспомогательные
- `_get_user_id(peer_id)` — внутренний user_id из peer_id
- `_ensure_user(peer_id, first_name)` — создание/обновление пользователя
- `_vk_send(peer_id, text, keyboard)` — отправка сообщения

## db.py

### Основные функции
- `init_db()` / `close_db()` — инициализация/закрытие БД
- `upsert_user()` — создание/обновление пользователя
- `get_or_create_user(message)` — из Message (TG или VK)
- `add_report()` — добавление отчёта со всеми полями
- `add_review()` — отзыв (rating 0-5, comment)
- `find_nearest_stations()` — ближайшие АЗС по координатам
- `find_stations_by_city()` — АЗС по городу
- `find_stations_by_name()` — поиск по названию
- `find_stations_by_address()` — поиск по адресу

### Реферальная система (20.07.2026)
- `get_referral_tier()` — возвращает тир по количеству активных рефералов
- `get_commission_rate(tier, is_top3=False)` — возвращает % комиссии
- `count_active_referrals(user_id)` — считает активных рефералов
- `calculate_user_tier(user_id)` — вычисляет тир на основе активных рефералов
- `get_user_referral_tier(user_id)` — возвращает тир из БД
- `update_all_tiers()` — обновляет тиры для всех пользователей
- `is_top3_referrer(user_id, month=None)` — проверяет вхождение в топ-3
- `calculate_top3(month)` — считает топ-3 за месяц
- `record_referral_commission(user_id, payment_amount, payment_id)` — записывает комиссию (multi-level)
- `check_self_referral(telegram_id, vk_id, code)` — антифрод
- `queue_referral_notification(user_id, type, message)` — ставит уведомление в очередь
- `get_unsent_notifications(limit)` — непосланные уведомления
- `mark_notification_sent(notification_id)` — помечает как отправленное
- `get_referral_leaderboard(limit=10)` — топ рефереров

### Константы реферальной системы
- `REFERRAL_TIERS` — словарь тиров с порогами и комиссиями
- `REFERRAL_TIER_NAMES` — русские названия тиров
- `REFERRAL_TOP3_COMMISSION = 70` — комиссия для топ-3
- `REFERRAL_LEVEL2_PERCENT = 5` — 2-й уровень
- `REFERRAL_LEVEL3_PERCENT = 3` — 3-й уровень (только топ-3)

### Premium
- `activate_premium()` — активация Premium
- `is_premium()` — проверка Premium
- `get_premium_info()` — информация о Premium
- `get_user_premium(uid)` — получение подписки
- `has_feature(tier, feature)` — проверка доступности фичи

### Password
- `hash_password(password)` — хеширование через pbkdf2_hmac
- `check_password(stored_hash, password)` — проверка пароля

## api.py

### Реферальные эндпоинты (20.07.2026)
- `GET /api/referral/tier` — тир реферера + прогресс + tiers info + next_tier
- `GET /api/referral/commission-rates` — все ставки комиссий (tiers + top3 + level2 + level3)
- `GET /api/referral/selling-texts` — продающие тексты (TG/VK/WhatsApp/друзьям)
- `GET /api/referral/leaderboard` — топ рефереров

### Premium эндпоинты
- `GET /api/premium-status` — статус Premium с tier
- `POST /api/premium/create-payment` — создание платежа YooMoney
- `GET /api/referral/code` — реферальный код
- `POST /api/referral/apply` — применение реферального кода (с антифродом)
- `GET /api/referral/balance` — баланс + приглашённые
- `POST /api/referral/withdraw` — заявка на вывод
- `GET /api/user/stats` — статистика пользователя

### Rate limits
- `RATE_LIMIT_GET = 30`
- `RATE_LIMIT_POST = 10`
- `RATE_LIMIT_ADMIN = 5`

## push_worker.py (20.07.2026)

### Основные функции
- `push_loop(bot)` — главный цикл + ежемесячный cron (1-е число)
- `_push_iteration(bot)` — итерация push-уведомлений
- `_process_referral_notifications(bot)` — обработка очереди реферальных уведомлений
- `run_monthly_referral_cron(bot)` — ежемесячный cron: топ-3 + пересчёт тиров + уведомления

## Flutter App (flutter-app/)

### Точка входа
- `lib/main.dart` — SplashScreen → RegistrationScreen/GuestMode → MainScreen

### Авторизация
- `lib/screens/registration_screen.dart` — _register(), _login(), _skip()
- **Критично**: Flutter хранит `telegramId` (не `userId`) для API запросов
- `StorageService.userId` = internal id, `StorageService.telegramId` = для API
- `ApiService.setUserId(telegramId)` — устанавливает `telegram_id` параметр

### API Service
- `lib/services/api_service.dart` — 40+ эндпоинтов
- `_get()` / `_post()` — с `telegram_id` параметром автоматически
- `registerUser(body)` — longTimeout (60s)
- `loginUser(name, password)` — longTimeout (60s)

### Модель станции
- `lib/models/station.dart` — `Station.fromJson()` парсит `statuses[]` массив
- Не использует `prices{}` или `availability{}` — бэкенд отдаёт `statuses[]`

### Карта
- `lib/screens/map_screen.dart` — flutter_map + OpenStreetMap
- `_loadStations(lat, lon)` — загружает станции при движении карты
- Маркеры: зелёный (available), жёлтый (partial), красный (unavailable), серый (unknown)

### Бэкенд: пароль
- Колонка `users.password_hash` — формат: `{hash_hex}:{salt_hex}`
- Хеш: hashlib.pbkdf2_hmac('sha256', password, salt, 100000)
- Проверка: split(':') → rebuild hash → compare hex
