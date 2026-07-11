# Деплой и управление

## Render (Frankfurt)
- **Сервис:** benzin-ryadom.onrender.com (только web service)
- **Автодеплой:** при пуше в main
- **Free tier:** засыпает через 15 мин idle → UptimeRobot пингует каждые 5 мин

### Сервисы Render
1. **benzin-ryadom** (web) — бот + API

### Cron — НЕ на Render
Render cron сервис НЕ используется (не создан). Вместо него — **GitHub Actions**.

## GitHub Actions cron
- **Workflow:** `.github/workflows/cron-parsers.yml`
- **Расписание:** каждый час (`0 * * * *`)
- **Что делает:** будит Render API + запускает `GET /api/parse?key=$PARSE_API_KEY`
- **Секрет:** `PARSE_API_KEY` = `benzin-parse` (Settings → Secrets → Actions)

Render Free засыпает — workflow делает 6 попыток health-check с паузой 10 сек, потом парсит.

### VPS
VPS (89.108.78.142) — НЕ используется для парсеров. Crontab очищен (`crontab -r`).

### Ручной деплой
```bash
cd "/Users/artem/Desktop/code/бензин рядом"
git add -A
git commit -m "fix: ..."
git push origin main
```
Render подхватит автоматически (autoDeploy: true).

### Проверка после деплоя
```bash
# Здоровье
curl -s "https://benzin-ryadom.onrender.com/api/health"

# Статистика
curl -s "https://benzin-ryadom.onrender.com/api/admin/stats" | python3 -m json.tool

# Поиск
curl -s "https://benzin-ryadom.onrender.com/api/search?q=Лукойл" | python3 -m json.tool

# Логи (последние 50 строк)
curl -s "https://benzin-ryadom.onrender.com/api/logs?lines=50" | python3 -m json.tool

# Запуск парсеров
curl -s "https://benzin-ryadom.onrender.com/api/parse?key=benzin-parse"

# Обогащение адресов
curl -s "https://benzin-ryadom.onrender.com/api/enrich?key=benzin-parse"

# Тест отчёта с новыми полями
curl -s -X POST "https://benzin-ryadom.onrender.com/api/reports" \
  -H "Content-Type: application/json" \
  -d '{"station_id": 1, "fuel_type": "92", "available": true, "telegram_id": 12345, "price": 55.40, "canister_ban": true, "limit_per_visit": 30}'

# Тест отзыва
curl -s -X POST "https://benzin-ryadom.onrender.com/api/reviews" \
  -H "Content-Type: application/json" \
  -d '{"station_id": 1, "fuel_type": "92", "rating": 5, "telegram_id": 12345, "comment": "Отличный бензин!"}'
```

## Supabase
- Dashboard: https://supabase.com/dashboard
- PostgreSQL connection string в Render env var `DATABASE_URL`
- Schema: `db/schema.sql`

### Полезные SQL запросы
```sql
-- Статистика парсеров
SELECT source, COUNT(*), MAX(created_at) FROM reports GROUP BY source ORDER BY 2 DESC;

-- Live отчёты
SELECT COUNT(*) FROM reports WHERE expires_at > NOW();

-- Live по городам
SELECT COUNT(DISTINCT s.city) FROM stations s
JOIN reports r ON r.station_id = s.id WHERE r.expires_at > NOW();

-- С лимитами/канистрами
SELECT source, COUNT(*) FROM reports
WHERE expires_at > NOW() AND (has_limit = true OR canister_ban = true)
GROUP BY source;

-- С очередями
SELECT source, COUNT(*) FROM reports
WHERE expires_at > NOW() AND queue_size IS NOT NULL
GROUP BY source;

-- АЗС с адресом
SELECT COUNT(*) FROM stations WHERE address IS NOT NULL AND address != '';

-- Отзывы
SELECT s.name, r.fuel_type, r.rating, r.comment, r.created_at
FROM reviews r JOIN stations s ON s.id = r.station_id
ORDER BY r.created_at DESC LIMIT 20;
```

## UptimeRobot
- Мониторит /api/health каждые 5 мин
- Free план: 50 мониторов
- Не даёт Render Free заснуть
