create index if not exists paper_fills_order_id_idx on paper_fills(order_id, fill_time_ms desc);
create index if not exists paper_positions_account_id_idx on paper_positions(account_id, updated_at desc);
create index if not exists paper_positions_decision_id_idx on paper_positions(decision_id);
create index if not exists paper_positions_order_id_idx on paper_positions(order_id);
create index if not exists alert_delivery_attempts_decision_id_idx on alert_delivery_attempts(decision_id, created_at desc);
