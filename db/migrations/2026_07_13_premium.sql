-- =====================================================
-- Premium подписки — 13.07.2026
-- 3 тарифа: economy (100₽), standard (250₽), elite (500₽)
-- =====================================================

CREATE TABLE IF NOT EXISTS premium_users (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier TEXT NOT NULL CHECK (tier IN ('economy', 'standard', 'elite')),
    started_at TIMESTAMPTZ DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    payment_id TEXT,                            -- ID платежа (ЮKassa/СБП/ручной)
    payment_amount INTEGER,                     -- в рублях
    payment_method TEXT,                        -- 'yukassa', 'sbp', 'manual', 'trial'
    is_active BOOLEAN DEFAULT TRUE,
    cancelled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_premium_users_user_id ON premium_users (user_id);
CREATE INDEX IF NOT EXISTS idx_premium_users_active ON premium_users (user_id, is_active, expires_at);

-- Лог платежей
CREATE TABLE IF NOT EXISTS premium_payments (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tier TEXT NOT NULL,
    amount INTEGER NOT NULL,
    currency TEXT DEFAULT 'RUB',
    status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed', 'refunded')),
    payment_method TEXT,
    external_id TEXT,                           -- ID в платёжной системе
    created_at TIMESTAMPTZ DEFAULT NOW(),
    paid_at TIMESTAMPTZ,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_premium_payments_user_id ON premium_payments (user_id);
CREATE INDEX IF NOT EXISTS idx_premium_payments_external_id ON premium_payments (external_id);

-- Триггер updated_at
DROP TRIGGER IF EXISTS update_premium_users_updated_at ON premium_users;
CREATE TRIGGER update_premium_users_updated_at
    BEFORE UPDATE ON premium_users
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
