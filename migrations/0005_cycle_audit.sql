create table if not exists cycle_runs (
    cycle_id text primary key,
    started_at_ms bigint not null,
    finished_at_ms bigint,
    status text not null check(status in ('RUNNING','COMPLETED','PARTIAL','FAILED')),
    mode text not null,
    symbols_requested int not null default 0 check(symbols_requested >= 0),
    symbols_processed int not null default 0 check(symbols_processed >= 0),
    decisions_count int not null default 0 check(decisions_count >= 0),
    orders_created int not null default 0 check(orders_created >= 0),
    error_count int not null default 0 check(error_count >= 0),
    code_version text not null,
    config_version text not null,
    summary jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists cycle_runs_started_idx on cycle_runs(started_at_ms desc);
create index if not exists cycle_runs_status_idx on cycle_runs(status, started_at_ms desc);

create table if not exists cycle_events (
    event_id uuid primary key default gen_random_uuid(),
    cycle_id text not null references cycle_runs(cycle_id) on delete cascade,
    sequence_id bigint not null,
    symbol text,
    stage text not null,
    status text not null,
    event_time_ms bigint not null,
    duration_ms bigint,
    reason_codes jsonb not null default '[]'::jsonb,
    metrics jsonb not null default '{}'::jsonb,
    error_type text,
    error_message text,
    created_at timestamptz not null default now(),
    unique(cycle_id, sequence_id)
);

create index if not exists cycle_events_lookup_idx on cycle_events(cycle_id, event_time_ms, sequence_id);
create index if not exists cycle_events_stage_idx on cycle_events(stage, status, event_time_ms desc);
