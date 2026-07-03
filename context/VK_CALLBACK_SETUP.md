# VK Callback API — настройка

## Что это

Callback API — это webhook для получения событий от сообщества VK. Альтернатива Long Poll. Более надёжная для продакшна.

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

## Как это работает

```
VK → POST /api/vk/callback
       ↓
   [verify secret]
       ↓
   type=confirmation → return VK_CONFIRMATION_TOKEN
   type=message_new → process_message_new() → send via VK API
   type=message_event → process_message_event() → ack
```

## Файлы

- `bot/vk_callback.py` — обработчики сообщений (start, find, search, profile, etc.)
- `bot/api.py::handle_vk_callback` — webhook endpoint
- `bot/vk_bot.py` — Long Poll (отключается через `VK_CALLBACK_ENABLED`)

## Отладка

Проверить что endpoint работает:
```bash
curl -X POST https://benzin-ryadom.onrender.com/api/vk/callback \
  -H "Content-Type: application/json" \
  -d '{"type":"confirmation"}'
```

Должен вернуть строку подтверждения.

## Текущее состояние

✅ Endpoint создан (`POST /api/vk/callback`)
✅ Confirmation handling
✅ Secret verification
✅ message_new → process_message_new
✅ message_event → process_message_event
✅ Базовые команды: /start, /help, /find, /subscribe, /profile, /donate
✅ Поиск по тексту (город, название, адрес)
⚠️ Inline-кнопки (callback) — минимальная поддержка, для полноценной работы нужна доработка

## Что осталось (опционально)

- [ ] Полная поддержка callback-кнопок в меню
- [ ] Обработка геолокации
- [ ] Старые сложные handlers (отчёты, подписки) — сейчас только базовые
- [ ] Replay protection (проверка event_id для дедупликации)
