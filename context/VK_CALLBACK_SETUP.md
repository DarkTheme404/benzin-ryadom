# VK Callback API — настройка

## Что это

Callback API — webhook для получения событий от сообщества VK. Альтернатива Long Poll. Более надёжная для продакшна.

## Возможности (после доработки)

- ✅ **Callback-кнопки** — `type: "callback"` с `payload` (вместо текстовых)
- ✅ **`messages.sendMessageEventAnswer`** — обязательный ack в течение 5 сек
- ✅ **Геолокация** — `handle_geo` для поиска ближайших АЗС и подписки на завоз
- ✅ **Flow отчёта** — callback-based: АЗС → топливо → статус
- ✅ **Flow отзыва** — callback-based: АЗС → топливо → звёзды
- ✅ **Подписка на АЗС** — `add_subscription(kind="station", target_id=...)`
- ✅ **State management с TTL** — 30 минут на состояние пользователя
- ✅ **Deduplication** — по `event_id` (callback) и `message.id` (message_new)
- ✅ **Secret verification** — если задан `VK_CALLBACK_SECRET`
- ✅ **Long Poll отключается** через `VK_CALLBACK_ENABLED=true`

## Регистрация в VK

1. Откройте сообщество: https://vk.com/benzyn_ryadom
2. **Управление → Настройки → Работа с API → Callback API**
3. Включите Callback API
4. Заполните:
   - **Адрес**: `https://benzin-ryadom.onrender.com/api/vk/callback`
   - **Секретный ключ**: придумайте сложный (например, `vk-callback-secret-2026-brzr`)
   - **Строка подтверждения**: придумайте (например, `brzr2026abc`)
5. Выберите события:
   - ✅ `message_new` — новые сообщения
   - ✅ `message_event` — нажатия inline-кнопок (callback)
6. Нажмите **«Подтвердить»** — VK отправит confirmation event

## Конфигурация (Render)

Добавьте в Environment Variables:

```
VK_CALLBACK_ENABLED=true
VK_CONFIRMATION_TOKEN=brzr2026abc
VK_CALLBACK_SECRET=vk-callback-secret-2026-brzr
```

После установки:
- Long Poll **автоматически отключится** (чтобы не дублировать ответы)
- Все события будут идти через webhook

## Архитектура callback-кнопок

Payload — компактный JSON: `{"a": "action", ...args}`

### Поддерживаемые actions:

| Action | Args | Что делает |
|--------|------|------------|
| `home` | — | Главное меню |
| `find` | — | Выбор города |
| `help` | — | Помощь |
| `profile` | — | Профиль |
| `donate` | — | Донат |
| `owner` | — | Инфо о владельце |
| `subscribe` | — | Подписка по гео |
| `check_sub` | — | Перепроверка подписки VK |
| `city` | `c: <name>` | Поиск АЗС в городе |
| `city_input` | — | Ввод города вручную |
| `report_start` | — | Начать flow отчёта |
| `report` | `s: <station_id>` | Выбор топлива для отчёта |
| `report_fuel` | `s, f` | Подтвердить топливо |
| `report_status` | `s, f, v` | Подтвердить статус (yes/low/no) |
| `review` | `s` | Начать flow отзыва |
| `review_fuel` | `s, f` | Выбор топлива для отзыва |
| `review_rating` | `s, f, r` | Подтвердить рейтинг (1-5) |
| `sub_station` | `s` | Подписаться на АЗС |
| `sub_radius` | `r` | Изменить радиус подписки |
| `station` | `s` | Вернуться к карточке АЗС |
| `open_app` | — | Ссылка на Mini App |

## Поток данных

```
VK → POST /api/vk/callback
       ↓
   [verify secret если задан]
       ↓
   type=confirmation → return VK_CONFIRMATION_TOKEN
   type=message_new  → process_message_new()
   type=message_event → process_message_event()
                         ↓
                       _vk_send_event_answer() (обязательно < 5с)
                         ↓
                       handler(payload) → _vk_send()
```

## Тесты

```bash
# Confirmation (нужен правильный токен)
curl -X POST https://benzin-ryadom.onrender.com/api/vk/callback \
  -H "Content-Type: application/json" -d '{"type":"confirmation"}'

# message_new
curl -X POST https://benzin-ryadom.onrender.com/api/vk/callback \
  -H "Content-Type: application/json" \
  -d '{"type":"message_new","object":{"message":{"peer_id":123456,"text":"/start","id":1}}}'

# message_event (callback кнопка)
curl -X POST https://benzin-ryadom.onrender.com/api/vk/callback \
  -H "Content-Type: application/json" \
  -d '{
    "type":"message_event",
    "object":{
      "event_id":"unique123",
      "user_id":123456,
      "peer_id":123456,
      "payload":"{\"a\":\"city\",\"c\":\"Иваново\"}",
      "conversation_message_id":1
    }
  }'
```

## Файлы

- `bot/vk_callback.py` — обработчики (полная логика)
- `bot/vk_keyboards.py` — клавиатуры (все callback-based)
- `bot/api.py::handle_vk_callback` — webhook endpoint
- `bot/vk_bot.py` — Long Poll (отключается через `VK_CALLBACK_ENABLED`)
- `bot/vk_keyboards.py::_callback_button` — helper для callback-кнопок

## Преимущества callback-кнопок

- **Нет мусора** в чате — нажатие не создаёт новое сообщение
- **Spinner** — пользователь видит индикатор загрузки
- **Toast** — короткое уведомление после действия
- **Стабильные ID** — payload не зависит от текста кнопки
- **Быстрее** — нет roundtrip на отправку сообщения

## Известные ограничения

- Inline callback buttons нельзя использовать в одной клавиатуре с text buttons (VK ограничение)
- State хранится в памяти (in-memory dict) — теряется при рестарте Render
- Для уведомлений о завозе нужен push worker, который сейчас работает через TG

## Roadmap (что можно добавить)

- [ ] Inline-кнопки для навигации по списку АЗС в городе
- [ ] Пагинация (page=2, page=3)
- [ ] Inline search (ввод с автодополнением)
- [ ] State persistence в БД (вместо in-memory)
- [ ] Поддержка attachments (фото отчётов)
