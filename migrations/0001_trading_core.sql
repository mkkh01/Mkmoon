create extension if not exists pgcrypto;

create table if not exists exchange_info_snapshots (
    id uuid primary key default gen_random_uuid(),
    symbol text not null,
    payload jsonb not null,
    captured_at timestamptz not null default now(),
    version text not null,
    unique(symbol, version)
);

create table if not exists candles (
    id bigserial primary key,
    symbol text not null,
    timeframe text not null,
    open_time_ms bigint not null,
    close_time_ms bigint not null,
    open_price numeric(38, 18) not null,
    high_price numeric(38, 18) not null,
    low_price numeric(38, 18) not null,
    close_price numeric(38, 18) not null,
    volume numeric(38, 18) not null,
    source_id text not null,
    ingested_at timestamptz not null default now(),
    unique(symbol, timeframe, open_time_ms),
    check(high_price >= greatest(open_price, close_price)),
    check(low_price <= least(open_price, close_price)),
    check(volume >= 0)
);

create index if not exists candles_lookup_idx on candles(symbol, timeframe, open_time_ms desc);

create table if not exists decisions (
    decision_id text primary key,
    symbol text not null,
    decision_time_ms bigint not null,
    data_cutoff_ms bigint not null,
    status text not null,
    payload jsonb not null,
    decision_hash text not null,
    code_version text not null,
    feature_version text not null,
    config_version text not null,
    created_at timestamptz not null default now(),
    unique(symbol, data_cutoff_ms, decision_hash)
);

create index if not exists decisions_symbol_time_idx on decisions(symbol, decision_time_ms desc);

create table if not exists decision_events (
    event_id uuid primary key default gen_random_uuid(),
    aggregate_id text not null,
    sequence_id bigint not null,
    event_type text not null,
    event_time_ms bigint not null,
    payload jsonb not null,
    schema_version text not null,
    created_at timestamptz not null default now(),
    unique(aggregate_id, sequence_id)
);

create table if not exists paper_orders (
    order_id text primary key,
    decision_id text not null references decisions(decision_id),
    symbol text not null,
    side text not null check(side = 'BUY'),
    order_type text not null,
    status text not null,
    requested_quantity numeric(38, 18) not null,
    requested_price numeric(38, 18) not null,
    filled_quantity numeric(38, 18) not null default 0,
    average_fill_price numeric(38, 18),
    fees numeric(38, 18) not null default 0,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists risk_reservations (
    reservation_id text primary key,
    decision_id text not null references decisions(decision_id),
    symbol text not null,
    risk_cash numeric(38, 18) not null,
    status text not null check(status in ('RESERVED','RELEASED','CONSUMED')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists replay_runs (
    run_id uuid primary key default gen_random_uuid(),
    code_version text not null,
    feature_version text not null,
    config_version text not null,
    data_snapshot_id text not null,
    result jsonb not null,
    created_at timestamptz not null default now()
);
