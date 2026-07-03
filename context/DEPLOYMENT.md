# Деплой и управление

## Render (Frankfurt)
- **Сервис:** benzin-ryadom.onrender.com (web service + cron)
- **Автодеплой:** при пуше в main
- **Free tier:** засыпает через 15 мин idle → UptimeRobot пингует каждые 5 мин

### Сервисы Render
1. **benzin-ryadom** (web) — бот + API
2. **benzin-parser-cron** (cron) — парсеры каждый час
3. **benzin-monitor** (cron) — мониторинг свежести каждые 2 часа

### Ручной деплой
```bash
cd "/Users/artem/Desktop/code/бензин рядом"
git add -A
git commit -m "fix: ..."
git push origin main
```
Render подхватит автоматически.

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
```

## VPS (89.108.78.142)
- hostname: cv7497839
- Проект: /opt/benzin-ryadom
- Crontab: парсеры каждый час
- Telegram заблокирован в РФ → TG парсер не работает на VPS

### SSH
```bash
ssh root@89.108.78.142
cd /opt/benzin-ryadom
```

### Crontab на VPS
```bash
crontab -l
# 0 * * * * cd /opt/benzin-ryadom && python3 scripts/orchestrator.py --once >> /var/log/benzin-cron.log 2>&1
```

## Supabase
- Dashboard: https://supabase.com/dashboard
- PostgreSQL connection string в Render env var `DATABASE_URL`
- Schema: `db/schema.sql`

### Полезные SQL запросы
```sql
-- Статистика парсеров
SELECT source, COUNT(*), MAX(created_at) FROM reports GROUP BY source;

-- АЗС с адресом
SELECT COUNT(*) FROM stations WHERE address IS NOT NULL AND address != '';

-- АЗС без адреса (с координатами)
SELECT COUNT(*) FROM stations WHERE (address IS NULL OR address = '') AND lat IS NOT NULL;

-- Отзывы
SELECT s.name, r.fuel_type, r.rating, r.comment, r.created_at
FROM reviews r JOIN stations s ON s.id = r.station_id
ORDER BY r.created_at DESC LIMIT 20;
```

## UptimeRobot
- Мониторит /api/health каждые 5 мин
- Free план: 50 мониторов
