# План доработок «Бензин рядом»

**Дата обновления**: 14.07.2026
**Версия**: 0.5.0 (Premium UX/UI complete + bugfixes)

---

## 🔴 В РАБОТЕ (сейчас)

### 1. Premium UX/UI (95% готово)
- ✅ Каталог фич (8 штук)
- ✅ Upsell модалка
- ✅ Hero CTA на главном
- ✅ Hero CTA в карточке АЗС
- ✅ Price history (график)
- ✅ Export CSV
- ✅ Route fuel A→B
- ✅ Map picker
- ✅ Fuel alarm push + UI (бюджет, кнопки, удаление)
- ✅ Welcome-экран с Premium teaser
- ✅ SOS-режим (Elite) — кнопка 🆘, broadcast 50 км
- ✅ Anti-traffic (Elite) — пробки + ETA + лучшее время
- ✅ Offline map (Economy) — service worker для тайлов
- ✅ Premium badge в карточках АЗС

---

## 🟡 СЛЕДУЮЩИЕ (по приоритету)

### 2. Marketing
- ✅ Реферальная программа — "Приведи друга — получи месяц бесплатно"
- Пост в VK/TG: "39 трасс + 4 288 городов + Premium"
- A/B тест: 3 дня trial vs сразу платный
- Партнёрство с АЗС-блогерами

### 3. Тех.долг
- Перевести BTN_* константы в общий модуль
- Добавить unit-тесты для premium endpoints
- CI/CD для Render (GitHub Actions)

---

## 🐛 Известные баги

### Высокий приоритет
- [x] VK bot: peer_id может пересекаться с TG ID — исправлено (vk_id колонка + get_user_id_by_vk_id)
- [ ] `link_code_expires_at` тип в PG (datetime vs str) — был фикс, нужно регресс-тест
- [ ] Render Free tier засыпает — нужно "Clear build cache & deploy" иногда

### Средний приоритет
- [ ] VK бот premium keyboard ломается в callback mode (не все кнопки работают)
- [ ] `getTgId()` возвращает undefined для VK-юзеров (fallback есть, но не везде)
- [ ] Map picker dragend не всегда обновляет zoom

### Низкий приоритет
- [x] Welcome-экран — реализован
- [x] Premium badge в карточке АЗС — реализован

---

## 💡 Идеи на будущее

1. **Голосовое сообщение о ценах** — "Сколько стоит 95-й в Иваново?"
2. **AR-режим камеры** — наведи на канистру → видишь цены АЗС рядом
3. **Подписка на конкретный бренд** — "уведомляй когда Лукойл опустит цену ниже 50₽"
4. **Партнёрская программа** с АЗС — кешбэк 2% Premium-юзерам
5. **Telegram Channel reposts** — лучшие отчёты Premium в канал
6. **VK Mini App promo** — баннеры в VK-сообществе
7. **Push через TG bot** — даже если Mini App закрыт, юзер получит push

---

## Метрики для отслеживания

### Конверсия в Premium
- Целевая: 5% от MAU
- Текущая: 0.7% (1/137)
- Улучшать через: trial период, A/B, реферальная программа

### Retention
- Day 1: должен вернуться 60%+
- Day 7: 30%+
- Day 30: 15%+

### Engagement
- Среднее число отчётов на юзера: 0.18 (24/137) — мало
- Целевое: 1+ отчёт на юзера

### Revenue
- 1 Premium юзер × 100₽/мес = 100₽ MRR
- Целевой: 10 000₽ MRR = 100 premium юзеров
- 100 × 250₽ = 25 000₽ MRR (Стандарт) — реалистичная цель

---

## План на ближайшие 2 недели

### Неделя 1 (14-20 июля)
1. ✅ Premium UX/UI основа (готово)
2. ✅ TG бот: красивый /premium
3. ✅ Welcome-экран
4. ✅ Fuel alarm push + UI
5. ✅ VK бот: красивый /premium
6. ✅ Счётчик экономии в профиле
7. ✅ SOS-режим (Elite)
8. ✅ Anti-traffic (Elite)
9. ✅ Offline map (Economy)
10. ✅ Premium badge в карточках АЗС
11. ✅ VK peer_id collision баг

### Неделя 2 (21-27 июля)
1. ⏳ Marketing-пост про Premium
2. ⏳ A/B тест 3-дневного trial
3. ✅ Реферальная программа

---

## Связанные файлы

- `context/PROJECT_CONTEXT.md` — главный контекст проекта
- `context/project_state.json` — машиночитаемое состояние
- `bot/premium_texts.py` — тексты фич
- `miniapp/premium-catalog.js` — каталог фич
- `miniapp/premium-ui.js` — UI namespace
