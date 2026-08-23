create table if not exists paper_accounts (
    account_id text primary key,
    quote_currency text not null default 'USDT',
    starting_cash numeric(38,18) not null,
    cash_balance numeric(38,18) not null,
    equity numeric(38,18) not null,
    reserved_cash numeric(38,18) not null default 0,
    realized_pnl numeric(38,18) not null default 0,
    updated_at_ms bigint not null,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    check(starting_cash >= 0),
    check(cash_balance >= 0),
    check(equity >= 0),
    check(reserved_cash >= 0)
);

create table if not exists paper_positions (
    position_id text primary key,
    account_id text not null references paper_accounts(account_id),
    order_id text not null references paper_orders(order_id),
    decision_id text not null references decisions(decision_id),
    symbol text not null,
    state text not null,
    quantity numeric(38,18) not null,
    entry_price numeric(38,18) not null,
    stop_price numeric(38,18) not null,
    target_price numeric(38,18) not null,
    entry_fee numeric(38,18) not null default 0,
    exit_price numeric(38,18),
    exit_fee numeric(38,18) not null default 0,
    realized_pnl numeric(38,18),
    opened_at_ms bigint not null,
    closed_at_ms bigint,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check(quantity > 0),
    check(entry_price > 0),
    check(stop_price > 0),
    check(target_price > entry_price),
    check(state in ('ENTERED','ACTIVE','PROTECTED','EXITING','CLOSED','UNKNOWN'))
);

create index if not exists paper_positions_symbol_state_idx on paper_positions(symbol, state, updated_at desc);
create unique index if not exists paper_orders_decision_id_uniq on paper_orders(decision_id);
create unique index if not exists risk_reservations_decision_id_uniq on risk_reservations(decision_id);
create unique index if not exists paper_positions_one_active_per_symbol_idx
    on paper_positions(symbol) where state in ('ENTERED','ACTIVE','PROTECTED','EXITING');

insert into paper_accounts(account_id, quote_currency, starting_cash, cash_balance, equity, updated_at_ms, payload)
values('paper:default', 'USDT', 10000, 10000, 10000, (extract(epoch from now()) * 1000)::bigint, '{"source":"migration:0006"}'::jsonb)
on conflict(account_id) do nothing;
