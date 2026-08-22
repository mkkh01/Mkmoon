from enum import StrEnum


class SignalState(StrEnum):
    FORMING = "FORMING"
    READY = "READY"
    TRIGGERED = "TRIGGERED"
    EXECUTABLE = "EXECUTABLE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"


class OrderState(StrEnum):
    PLANNED = "PLANNED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class PositionState(StrEnum):
    FLAT = "FLAT"
    ENTERED = "ENTERED"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    PROTECTED = "PROTECTED"
    EXITING = "EXITING"
    CLOSED = "CLOSED"
    UNKNOWN = "UNKNOWN"


class InvalidTransition(RuntimeError):
    pass


SIGNAL_TRANSITIONS = {
    (SignalState.FORMING, "SETUP_READY"): SignalState.READY,
    (SignalState.READY, "VALID_TRIGGER"): SignalState.TRIGGERED,
    (SignalState.TRIGGERED, "EXECUTION_FEASIBLE"): SignalState.EXECUTABLE,
    (SignalState.READY, "TTL_EXPIRED"): SignalState.EXPIRED,
    (SignalState.TRIGGERED, "TOO_LATE"): SignalState.EXPIRED,
    (SignalState.READY, "INVALIDATION"): SignalState.INVALIDATED,
    (SignalState.TRIGGERED, "INVALIDATION"): SignalState.INVALIDATED,
    (SignalState.EXECUTABLE, "CANCEL"): SignalState.CANCELLED,
}

ORDER_TRANSITIONS = {
    (OrderState.PLANNED, "SUBMIT"): OrderState.SUBMITTED,
    (OrderState.SUBMITTED, "PARTIAL_FILL"): OrderState.PARTIALLY_FILLED,
    (OrderState.SUBMITTED, "FILL"): OrderState.FILLED,
    (OrderState.PARTIALLY_FILLED, "FILL"): OrderState.FILLED,
    (OrderState.SUBMITTED, "REJECT"): OrderState.REJECTED,
    (OrderState.SUBMITTED, "CANCEL_REQUEST"): OrderState.CANCEL_PENDING,
    (OrderState.PARTIALLY_FILLED, "CANCEL_REQUEST"): OrderState.CANCEL_PENDING,
    (OrderState.CANCEL_PENDING, "CANCEL_ACK"): OrderState.CANCELLED,
    (OrderState.SUBMITTED, "UNKNOWN_EXECUTION"): OrderState.UNKNOWN,
    (OrderState.CANCEL_PENDING, "UNKNOWN_EXECUTION"): OrderState.UNKNOWN,
}

POSITION_TRANSITIONS = {
    (PositionState.FLAT, "FIRST_FILL"): PositionState.ENTERED,
    (PositionState.ENTERED, "ACTIVATE"): PositionState.ACTIVE,
    (PositionState.ACTIVE, "PARTIAL_EXIT"): PositionState.PARTIAL,
    (PositionState.PARTIAL, "PROTECT"): PositionState.PROTECTED,
    (PositionState.ACTIVE, "PROTECT"): PositionState.PROTECTED,
    (PositionState.ACTIVE, "EXIT_REQUEST"): PositionState.EXITING,
    (PositionState.PARTIAL, "EXIT_REQUEST"): PositionState.EXITING,
    (PositionState.PROTECTED, "EXIT_REQUEST"): PositionState.EXITING,
    (PositionState.EXITING, "CLOSED"): PositionState.CLOSED,
    (PositionState.ENTERED, "CLOSED"): PositionState.CLOSED,
}


def transition(current, event: str, transitions: dict[tuple, object]):
    key = (current, event)
    if key not in transitions:
        raise InvalidTransition(f"invalid transition: {current} + {event}")
    return transitions[key]
