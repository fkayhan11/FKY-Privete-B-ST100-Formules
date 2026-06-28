-- Fiscograph Supabase Database Initialization Script
-- Paste and execute this script in your Supabase SQL Editor.

-- 1. Create Companies Table
CREATE TABLE IF NOT EXISTS companies (
    symbol VARCHAR(15) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    sector VARCHAR(50) DEFAULT 'Unknown',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW())
);

-- 2. Create Fundamentals Table (with JSONB support)
CREATE TABLE IF NOT EXISTS fundamentals (
    symbol VARCHAR(15) REFERENCES companies(symbol) ON DELETE CASCADE,
    report_date DATE NOT NULL,
    metrics JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()),
    PRIMARY KEY (symbol, report_date)
);

-- 3. Create Prices History Table
CREATE TABLE IF NOT EXISTS prices (
    symbol VARCHAR(15) REFERENCES companies(symbol) ON DELETE CASCADE,
    price_date DATE NOT NULL,
    open NUMERIC(15, 4) NOT NULL,
    high NUMERIC(15, 4) NOT NULL,
    low NUMERIC(15, 4) NOT NULL,
    close NUMERIC(15, 4) NOT NULL,
    volume NUMERIC(20, 2) DEFAULT 0,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW()),
    PRIMARY KEY (symbol, price_date)
);

-- 4. Create Signal Results Table
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(15) REFERENCES companies(symbol) ON DELETE CASCADE,
    signal_date DATE NOT NULL,
    signal_type VARCHAR(10) NOT NULL CHECK (signal_type IN ('buy', 'sell')),
    score NUMERIC(5, 4),
    risk_level VARCHAR(10) CHECK (risk_level IN ('Low', 'Medium', 'High', 'Unknown')),
    suggested_weight_pct NUMERIC(5, 2),
    reason TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT TIMEZONE('utc'::text, NOW())
);

-- 5. Create Active Positions Table
CREATE TABLE IF NOT EXISTS scanner_positions (
    symbol VARCHAR(15) PRIMARY KEY REFERENCES companies(symbol) ON DELETE CASCADE,
    entry_price NUMERIC(15, 4) NOT NULL,
    highest_price NUMERIC(15, 4) NOT NULL,
    last_price NUMERIC(15, 4) NOT NULL,
    trailing_stop_price NUMERIC(15, 4),
    z_score NUMERIC(8, 4),
    atr14 NUMERIC(15, 4),
    entry_signal_at TIMESTAMP WITH TIME ZONE NOT NULL,
    last_updated_at TIMESTAMP WITH TIME ZONE NOT NULL
);

-- ── INDEXES FOR PEAK PERFORMANCE ───────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_fundamentals_metrics ON fundamentals USING gin (metrics);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices (price_date DESC);
CREATE INDEX IF NOT EXISTS idx_signals_date_type ON signals (signal_date DESC, signal_type);
CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signals (symbol);
