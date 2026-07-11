-- =====================================================
-- Миграция: routes + station_routes
-- Дата: 2026-07-12
-- Описание: Поиск АЗС по федеральным/региональным трассам РФ
-- =====================================================

-- Основная таблица трасс
CREATE TABLE IF NOT EXISTS routes (
    id BIGSERIAL PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,           -- M-7, M-10, "Дон", "Кавказ"
    name TEXT NOT NULL,                  -- Полное название
    aliases TEXT,                        -- Альтернативные названия через запятую
    type TEXT DEFAULT 'federal',         -- federal / regional / local
    length_km INTEGER,                   -- Протяжённость
    start_point TEXT,                    -- Начало трассы
    end_point TEXT,                      -- Конец трассы
    description TEXT,                    -- Описание
    lat_min DOUBLE PRECISION,            -- bbox для быстрой фильтрации
    lat_max DOUBLE PRECISION,
    lon_min DOUBLE PRECISION,
    lon_max DOUBLE PRECISION,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Добавляем bbox-колонки если таблица уже существует
ALTER TABLE routes ADD COLUMN IF NOT EXISTS lat_min DOUBLE PRECISION;
ALTER TABLE routes ADD COLUMN IF NOT EXISTS lat_max DOUBLE PRECISION;
ALTER TABLE routes ADD COLUMN IF NOT EXISTS lon_min DOUBLE PRECISION;
ALTER TABLE routes ADD COLUMN IF NOT EXISTS lon_max DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_routes_code ON routes (code);
CREATE INDEX IF NOT EXISTS idx_routes_name ON routes (name);

-- Связь АЗС с трассами (многие-ко-многим)
CREATE TABLE IF NOT EXISTS station_routes (
    station_id BIGINT NOT NULL,
    route_id BIGINT NOT NULL,
    km_marker INTEGER,                   -- Километр на трассе (если известен)
    side TEXT,                           -- left/right/both — какая сторона
    direction TEXT,                      -- forward/backward/both
    PRIMARY KEY (station_id, route_id),
    CONSTRAINT fk_station FOREIGN KEY (station_id) REFERENCES stations(id) ON DELETE CASCADE,
    CONSTRAINT fk_route FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_station_routes_route ON station_routes (route_id);
CREATE INDEX IF NOT EXISTS idx_station_routes_station ON station_routes (station_id);
CREATE INDEX IF NOT EXISTS idx_station_routes_km ON station_routes (route_id, km_marker);

COMMENT ON TABLE routes IS 'Федеральные/региональные трассы РФ';
COMMENT ON TABLE station_routes IS 'Связь АЗС с трассами и километровыми отметками';
