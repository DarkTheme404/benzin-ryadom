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

### FSM-машины
- `SubscribeStates` — подписки (waiting_geo → waiting_radius)
- `ReportAddressStates` — поиск по адресу (waiting_query)

### Callback-обработчики
- `report_start` — начало отчёта (filter: `^report:\d+$`)
- `report_fuel` — выбор топлива
- `report_submit` — отправка отчёта
- `report_city_callback` — выбор города для отчёта
- `report_address_start` — поиск по адресу
- `review_start` — начало отзыва
- `subscribe_station` — подписка на АЗС
- `show_station_details` — карточка АЗС (`st:`)
- `go_home_callback` — "В начало"
- `premium_trial_callback` — пробный Premium
- `buy_premium_callback` — покупка Premium

### Вспомогательные
- `_tg_id(obj)` — ID пользователя из Message или CallbackQuery
- `_ensure_callback_user(callback)` — создание/получение пользователя из callback
- `_require_subscription(message)` — проверка подписки TG
- `_require_subscription_callback(callback)` — проверка подписки через callback

## vk_bot.py (VK бот)

### Команды
- `cmd_start` — главное меню
- `cmd_find` — поиск АЗС
- `cmd_find_stations` — список АЗС по городу

### Текстовые обработчики (catch-all)
Все текстовые сообщения обрабатываются в `on_geo_and_text`:
- Кнопки главного меню
- Поиск по адресу (`awaiting_address_query`)
- Отзывы (`review_station`, `review_fuel`)
- Владелец (`_owner_waiting_role`, `_owner_waiting_inn`)
- Подписки

### Вспомогательные
- `_send(msg, text, keyboard)` — отправка сообщения
- `_uid(msg)` — ID пользователя
- `_require_sub(msg)` — проверка подписки VK
- `_check_vk_subscription(user_id, api)` — проверка через VK API
- `_user_state` — словарь состояний пользователей

## db.py

### Основные функции
- `init_db()` / `close_db()` — инициализация/закрытие БД
- `upsert_user()` — создание/обновление пользователя
- `get_or_create_user(message)` — из Message (TG или VK)
- `add_report()` — добавление отчёта
- `find_nearest_stations()` — ближайшие АЗС по координатам
- `find_stations_by_city()` — АЗС по городу
- `find_stations_by_name()` — поиск по названию
- `find_stations_by_address()` — поиск по адресу (с разбиением на слова)
- `get_station_current_status()` — текущий статус АЗС
- `stale_old_reports(source)` — удаление старых отчётов

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

## api.py

### Эндпоинты
- `/api/health` — здоровье
- `/api/admin/stats` — статистика
- `/api/search?q=` — поиск
- `/api/stations?lat=&lon=&fuel=` — ближайшие
- `/api/stations/by-city?city=&fuel=` — по городу
- `/api/stations/emergency?city=` — экстренный
- `/api/stations/{id}` — детали АЗС
- `/api/stations/{id}/prices` — цены по источникам
- `/api/reports` (POST) — создание отчёта
- `/api/price-update` (POST) — обновление цены
- `/api/parse?key=` — запуск парсеров
- `/api/enrich?key=` — обогащение адресов
- `/api/reverse-geocode?lat=&lon=` — геокодирование
- `/api/import_prices` (POST) — импорт от внешних парсеров

## utils.py

### Форматирование
- `format_station_card(station, statuses)` — карточка АЗС (сеть → адрес → рейтинг)
- `format_fuel_status(statuses)` — статус топлива
- `format_price(price)` — цена
- `format_time_ago(dt)` — "5 мин назад"
