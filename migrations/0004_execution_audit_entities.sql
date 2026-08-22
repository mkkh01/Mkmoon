create table if not exists paper_fills (
    fill_id text primary key,
    order_id text not null references paper_orders(order_id),
    symbol text not null,
    quantity numeric(38,18) not null,
    price numeric(38,18) not null,
    fee numeric(38,18) not null default 0,
    fill_time_ms bigint not null,
    payload jsonb not null,
    created_at timestamptz not null default now()
);

create table if not exists account_snapshots (
    snapshot_id text primary key,
    captured_at_ms bigint not null,
    quote_currency text not null,
    equity numeric(38,18) not null,
    balances jsonb not null,
    source text not null,
    created_at timestamptz not null default now()
);

create table if not exists signal_lifecycle_events (
    event_id uuid primary key default gen_random_uuid(),
    signal_id text not null,
    sequence_id bigint not null,
    state text not null,
    event_type text not null,
    event_time_ms bigint not null,
    payload jsonb not null,
    created_at timestamptz not null default now(),
    unique(signal_id, sequence_id)
);

create table if not exists alert_delivery_attempts (
    delivery_id text primary key,
    decision_id text not null references decisions(decision_id),
    channel text not null,
    status text not null,
    delivered_at_ms bigint,
    payload jsonb not null,
    created_at timestamptz not null default now()
);
