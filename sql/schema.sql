-- ============================================================
-- Mkmoon / M_DUAL — Supabase (Postgres) schema
-- مطابق حرفيًا لما يكتبه التطبيق في app/store.py
-- شغّله مرة واحدة في: Supabase Dashboard → SQL Editor → New query → Run
-- آمن للتكرار (idempotent): IF NOT EXISTS في كل مكان
-- ============================================================

-- 1) الإشارات/التوصيات — كل توصية صادرة
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    t BIGINT NOT NULL,
    symbol TEXT,
    tier TEXT,
    side INT,
    ctype TEXT,
    signal_t BIGINT,
    ord_t BIGINT,
    order_id TEXT,
    limit_px DOUBLE PRECISION,
    stop_px DOUBLE PRECISION,
    tp_px DOUBLE PRECISION,
    r0 DOUBLE PRECISION,
    risk_pct DOUBLE PRECISION,
    sweep_src TEXT,
    session TEXT,
    fvg_lo DOUBLE PRECISION,
    fvg_hi DOUBLE PRECISION,
    atr DOUBLE PRECISION,
    htf1 INT,
    htf2 INT,
    disp_ok BOOLEAN,
    z DOUBLE PRECISION,
    note TEXT
);

-- 2) الأوامر المعلقة — resting / filled / cancelled / expired
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    symbol TEXT,
    tier TEXT,
    side INT,
    limit_px DOUBLE PRECISION,
    stop_px DOUBLE PRECISION,
    tp_px DOUBLE PRECISION,
    r0 DOUBLE PRECISION,
    placed_t BIGINT,
    expire_t BIGINT,
    closed_t BIGINT,
    signal_t BIGINT,
    status TEXT
);

-- 3) الصفقات — دورة الحياة كاملة + سبب الإغلاق + حقوقية بعده
CREATE TABLE IF NOT EXISTS trades (
    trade_id TEXT PRIMARY KEY,
    order_id TEXT,
    symbol TEXT,
    tier TEXT,
    side INT,
    entry_t BIGINT,
    entry_px DOUBLE PRECISION,
    stop_px DOUBLE PRECISION,
    locked_stop DOUBLE PRECISION,
    tp_px DOUBLE PRECISION,
    r DOUBLE PRECISION,
    exit_t BIGINT,
    exit_px DOUBLE PRECISION,
    exit_reason TEXT,
    gross_r DOUBLE PRECISION,
    net_r DOUBLE PRECISION,
    fees DOUBLE PRECISION,
    mfe_r DOUBLE PRECISION,
    mae_r DOUBLE PRECISION,
    hold_h DOUBLE PRECISION,
    ambiguous INT,
    locked BOOLEAN,
    equity_pct DOUBLE PRECISION,
    signal_t BIGINT,
    ctype TEXT,
    session TEXT,
    status TEXT,
    leg TEXT,             -- M_DUAL: 'F' سريع | 'T' ملموس
    leg_ar TEXT,          -- الاسم العربي للجهة
    risk_pct DOUBLE PRECISION  -- مخاطرة هذه الجهة (%)
);

-- ترقية قاعدة قديمة إن وُجدت (لا تضر الجديدة)
ALTER TABLE trades ADD COLUMN IF NOT EXISTS leg TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS leg_ar TEXT;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS risk_pct DOUBLE PRECISION;

-- 4) تقارير الدورة — صف كل دقيقة: كل مفاصل النظام + الاتصالات
CREATE TABLE IF NOT EXISTS cycle_reports (
    id BIGSERIAL PRIMARY KEY,
    t BIGINT NOT NULL,
    cycle BIGINT,
    uptime_s BIGINT,
    verdict TEXT,
    detail TEXT
);

-- 5) سجل نبض/أعطال تليجرام
CREATE TABLE IF NOT EXISTS heartbeat_log (
    id BIGSERIAL PRIMARY KEY,
    t BIGINT NOT NULL,
    kind TEXT,
    msg TEXT
);

-- ------------------------------------------------------------
-- الفهارس (استعلامات البوت واللوحات)
-- ------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_trades_status   ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol   ON trades(symbol);
CREATE INDEX IF NOT EXISTS idx_trades_entry_t  ON trades(entry_t);
CREATE INDEX IF NOT EXISTS idx_trades_leg      ON trades(leg);
CREATE INDEX IF NOT EXISTS idx_orders_symbol_s ON orders(symbol, status);
CREATE INDEX IF NOT EXISTS idx_orders_placed_t ON orders(placed_t);
CREATE INDEX IF NOT EXISTS idx_signals_t       ON signals(t);
CREATE INDEX IF NOT EXISTS idx_cycle_t         ON cycle_reports(t);
CREATE INDEX IF NOT EXISTS idx_heartbeat_t     ON heartbeat_log(t);

-- ------------------------------------------------------------
-- (اختياري) عرض أداء يومي جاهز للاستعلام
-- ------------------------------------------------------------
CREATE OR REPLACE VIEW v_daily AS
SELECT
    to_timestamp(entry_t / 1000)::date AS day,
    leg,
    COUNT(*)                                          AS n,
    COUNT(*) FILTER (WHERE net_r > 0)                 AS wins,
    ROUND(AVG(net_r)::numeric, 4)                     AS exp_r,
    ROUND(SUM(net_r)::numeric, 2)                     AS sum_r,
    ROUND(MAX(equity_pct)::numeric, 4)                AS equity_end
FROM trades
WHERE status = 'closed'
GROUP BY 1, 2
ORDER BY 1 DESC, 2;

-- ------------------------------------------------------------
-- (اختياري لكن موصى به) حماية: منع anon، السماح لـ service_role فقط
-- التطبيق يتصل بـ DATABASE_URL (مالك الجداول = يتجاوز RLS تلقائيًا)
-- هذا القسم يحمي لو استخدمت REST/PostgREST بمفتاح anon لاحقًا
-- ------------------------------------------------------------
ALTER TABLE signals       ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders        ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades        ENABLE ROW LEVEL SECURITY;
ALTER TABLE cycle_reports ENABLE ROW LEVEL SECURITY;
ALTER TABLE heartbeat_log ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['signals','orders','trades','cycle_reports','heartbeat_log']
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS service_only ON %I', t);
        EXECUTE format(
            'CREATE POLICY service_only ON %I FOR ALL TO service_role USING (true) WITH CHECK (true)',
            t);
    END LOOP;
END $$;

-- انتهى — بعدها اضبط في Render:
--   DATABASE_URL = postgres://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres
--   (Supabase → Project Settings → Database → Connection string → URI)
