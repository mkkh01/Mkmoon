-- Internal trading tables are intentionally not exposed through the public REST API yet.
-- The Render backend uses the private PostgreSQL connection and remains the only writer.
revoke all on function public.rls_auto_enable() from anon, authenticated;

revoke all on table public.exchange_info_snapshots from anon, authenticated;
revoke all on table public.candles from anon, authenticated;
revoke all on table public.decisions from anon, authenticated;
revoke all on table public.decision_events from anon, authenticated;
revoke all on table public.paper_orders from anon, authenticated;
revoke all on table public.risk_reservations from anon, authenticated;
revoke all on table public.replay_runs from anon, authenticated;
