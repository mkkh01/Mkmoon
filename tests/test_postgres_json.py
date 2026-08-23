from app.storage.postgres import _decode_jsonb, _decode_row_json


def test_decode_jsonb_turns_json_strings_into_structures() -> None:
    assert _decode_jsonb('{"symbols_requested":25}') == {"symbols_requested": 25}
    assert _decode_jsonb('["WATCH", "SETUP_INCOMPLETE"]') == ["WATCH", "SETUP_INCOMPLETE"]
    assert _decode_jsonb({"already": "decoded"}) == {"already": "decoded"}


def test_decode_row_json_normalizes_only_declared_json_fields() -> None:
    row = {"cycle_id": "c1", "summary": '{"symbol_diagnostics": {"BTCUSDT": {}}}', "status": "COMPLETED"}
    result = _decode_row_json(row, ("summary",))
    assert result["cycle_id"] == "c1"
    assert result["status"] == "COMPLETED"
    assert result["summary"]["symbol_diagnostics"]["BTCUSDT"] == {}


def test_paper_position_and_order_json_fields_are_decoded() -> None:
    position = _decode_row_json({"payload": '{"risk_cash":"25"}'}, ("payload",))
    order = _decode_row_json({"payload": '{"mode":"paper"}', "fills": '[{"fill_id":"f1"}]'}, ("payload", "fills"))
    assert position["payload"]["risk_cash"] == "25"
    assert order["payload"]["mode"] == "paper"
    assert order["fills"][0]["fill_id"] == "f1"
