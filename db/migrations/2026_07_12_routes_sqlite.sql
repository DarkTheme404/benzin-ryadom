-- =====================================================
-- Миграция: routes + station_routes (SQLite)
-- =====================================================

CREATE TABLE IF NOT EXISTS routes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    aliases TEXT,
    type TEXT DEFAULT 'federal',
    length_km INTEGER,
    start_point TEXT,
    end_point TEXT,
    description TEXT,
    lat_min REAL,
    lat_max REAL,
    lon_min REAL,
    lon_max REAL,
    is_active INTEGER DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_routes_code ON routes (code);
CREATE INDEX IF NOT EXISTS idx_routes_name ON routes (name);

CREATE TABLE IF NOT EXISTS station_routes (
    station_id INTEGER NOT NULL,
    route_id INTEGER NOT NULL,
    km_marker INTEGER,
    side TEXT,
    direction TEXT,
    PRIMARY KEY (station_id, route_id),
    FOREIGN KEY (station_id) REFERENCES stations(id) ON DELETE CASCADE,
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_station_routes_route ON station_routes (route_id);
CREATE INDEX IF NOT EXISTS idx_station_routes_station ON station_routes (station_id);
CREATE INDEX IF NOT EXISTS idx_station_routes_km ON station_routes (route_id, km_marker);
