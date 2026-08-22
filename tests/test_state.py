import pytest

from app.core.state import InvalidTransition, OrderState, SignalState, transition, ORDER_TRANSITIONS, SIGNAL_TRANSITIONS


def test_signal_lifecycle_is_explicit() -> None:
    state = transition(SignalState.FORMING, "SETUP_READY", SIGNAL_TRANSITIONS)
    state = transition(state, "VALID_TRIGGER", SIGNAL_TRANSITIONS)
    assert transition(state, "EXECUTION_FEASIBLE", SIGNAL_TRANSITIONS) == SignalState.EXECUTABLE


def test_invalid_order_transition_is_rejected() -> None:
    with pytest.raises(InvalidTransition):
        transition(OrderState.FILLED, "SUBMIT", ORDER_TRANSITIONS)
