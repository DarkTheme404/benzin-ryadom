-- Миграция: увеличение life-time парсерских данных с 2ч до 24ч
-- Проблема: view station_current_status показывает парсерские данные только за 2 часа
-- Если парсер не отработал 2+ часа, данные пропадают из выдачи
-- Решение: увеличить до 24ч (парсеры обновляют данные каждый час)

DROP VIEW IF EXISTS station_current_status;
CREATE OR REPLACE VIEW station_current_status AS
SELECT DISTINCT ON (s.id, r.fuel_type)
    s.id AS station_id,
    s.name,
    s.lat,
    s.lon,
    s.operator,
    s.city,
    s.region,
    r.fuel_type,
    r.available,
    r.price,
    r.queue_size,
    r.has_limit,
    r.limit_liters,
    r.confidence,
    r.source,
    r.created_at AS last_report_at,
    EXTRACT(EPOCH FROM (NOW() - r.created_at)) AS seconds_since_report,
    CASE
        WHEN r.created_at > NOW() - INTERVAL '30 minutes' THEN 'fresh'
        WHEN r.created_at > NOW() - INTERVAL '2 hours' THEN 'recent'
        WHEN r.created_at > NOW() - INTERVAL '6 hours' THEN 'stale'
        WHEN r.created_at > NOW() - INTERVAL '24 hours' THEN 'outdated'
        ELSE 'expired'
    END AS freshness
FROM stations s
LEFT JOIN LATERAL (
    SELECT *
    FROM reports
    WHERE station_id = s.id
    AND (
        -- Парсерские отчёты: живут 24 часа (обновляются парсерами каждый час)
        (source != 'user' AND created_at > NOW() - INTERVAL '24 hours')
        OR
        -- Пользовательские отчёты: живут 7 дней или пока не противоречит парсер
        (source = 'user' AND created_at > NOW() - INTERVAL '7 days')
    )
    ORDER BY 
        CASE WHEN source = 'user' THEN 0 ELSE 1 END,
        confidence DESC, 
        created_at DESC
    LIMIT 1
) r ON true
WHERE s.is_active = TRUE;
