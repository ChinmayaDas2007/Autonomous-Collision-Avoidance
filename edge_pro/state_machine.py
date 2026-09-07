"""
Project Kessler - Edge Processing Payload
Payload Finite State Machine (FSM) (Phase 1)
-------------------------------------------------------------------------------
Provides deterministic lifecycle management for the Edge Payload node,
governing transitions between Ground AI ingestion, ADCS slew targeting,
and optical tracking.
"""

from enum import Enum, auto
import logging
import time
from typing import Callable, List, Optional, Set, Dict

logger = logging.getLogger("edge_pro.fsm")


class PayloadState(Enum):
    """Operational states of the Project Kessler Edge Payload."""
    BOOT = "BOOT"                         # System startup, socket initialization
    STANDBY = "STANDBY"                   # Listening for Ground AI Refined CDMs
    TARGET_SCHEDULED = "TARGET_SCHEDULED" # CDM ingested, observation window queued
    SLEWING = "SLEWING"                   # Slew command pushed to core_phy, ADCS slewing
    TRACKING = "TRACKING"                 # Optical sensor locked on threat, differencing active
    DECISION_EVALUATION = "DECISION_EVALUATION"  # Phase 4 Go/No-Go decision matrix evaluation
    MANEUVER_ARMED = "MANEUVER_ARMED"     # Avoidance burn authorized and dispatched
    MISSION_COMPLETE = "MISSION_COMPLETE" # Tracking & decision complete, telemetry broadcast
    FAULT = "FAULT"                       # Anomalous state, socket error or invalid contract


class StateTransitionError(RuntimeError):
    """Raised when an invalid state transition is requested."""
    pass


# Valid directed graph of state transitions
VALID_TRANSITIONS: Dict[PayloadState, Set[PayloadState]] = {
    PayloadState.BOOT: {PayloadState.STANDBY, PayloadState.FAULT},
    PayloadState.STANDBY: {PayloadState.TARGET_SCHEDULED, PayloadState.FAULT},
    PayloadState.TARGET_SCHEDULED: {
        PayloadState.SLEWING,
        PayloadState.STANDBY,   # Cancel / reschedule
        PayloadState.FAULT
    },
    PayloadState.SLEWING: {PayloadState.TRACKING, PayloadState.FAULT},
    PayloadState.TRACKING: {
        PayloadState.DECISION_EVALUATION,
        PayloadState.MISSION_COMPLETE,  # Backward compatible direct bypass
        PayloadState.FAULT
    },
    PayloadState.DECISION_EVALUATION: {
        PayloadState.MANEUVER_ARMED,
        PayloadState.MISSION_COMPLETE,  # When NO_MANEUVER_REQUIRED
        PayloadState.STANDBY,
        PayloadState.FAULT,
    },
    PayloadState.MANEUVER_ARMED: {
        PayloadState.MISSION_COMPLETE,
        PayloadState.STANDBY,
        PayloadState.FAULT,
    },
    PayloadState.MISSION_COMPLETE: {PayloadState.STANDBY, PayloadState.BOOT, PayloadState.FAULT},
    PayloadState.FAULT: {PayloadState.STANDBY, PayloadState.BOOT},
}


class PayloadStateMachine:
    """
    Manages the lifecycle state of the Edge Processing Payload.
    Enforces deterministic state transitions and dispatches event notifications.
    """

    def __init__(self, initial_state: PayloadState = PayloadState.BOOT):
        self._current_state = initial_state
        self._previous_state: Optional[PayloadState] = None
        self._transition_history: List[dict] = []
        self._listeners: List[Callable[[PayloadState, PayloadState, Optional[str]], None]] = []
        self._record_transition(None, initial_state, "Initial system boot")

    @property
    def current_state(self) -> PayloadState:
        return self._current_state

    @property
    def previous_state(self) -> Optional[PayloadState]:
        return self._previous_state

    def add_listener(self, listener: Callable[[PayloadState, PayloadState, Optional[str]], None]) -> None:
        """Subscribes a callback to state transition events."""
        self._listeners.append(listener)

    def can_transition_to(self, new_state: PayloadState) -> bool:
        """Checks if a transition to new_state is legally allowed from current state."""
        allowed = VALID_TRANSITIONS.get(self._current_state, set())
        return new_state in allowed

    def transition_to(self, new_state: PayloadState, reason: Optional[str] = None) -> None:
        """
        Executes a state transition if valid according to the operational graph.
        Raises StateTransitionError if the requested transition is illegal.
        """
        if not self.can_transition_to(new_state):
            err_msg = (
                f"Illegal state transition from [{self._current_state.value}] "
                f"to [{new_state.value}]. Reason given: '{reason}'"
            )
            logger.error(err_msg)
            raise StateTransitionError(err_msg)

        old_state = self._current_state
        self._previous_state = old_state
        self._current_state = new_state
        self._record_transition(old_state, new_state, reason)

        logger.info(
            f"FSM State Transition: [{old_state.value}] -> [{new_state.value}]"
            + (f" | Reason: {reason}" if reason else "")
        )

        for listener in self._listeners:
            try:
                listener(old_state, new_state, reason)
            except Exception as ex:
                logger.exception(f"Error in state transition listener: {ex}")

    def fault(self, reason: str) -> None:
        """Transitions payload directly into FAULT mode."""
        logger.warning(f"Payload fault triggered: {reason}")
        if self._current_state != PayloadState.FAULT:
            self._previous_state = self._current_state
            self._current_state = PayloadState.FAULT
            self._record_transition(self._previous_state, PayloadState.FAULT, reason)
            for listener in self._listeners:
                try:
                    listener(self._previous_state, PayloadState.FAULT, reason)
                except Exception as ex:
                    logger.exception(f"Error in fault listener: {ex}")

    def reset_to_standby(self, reason: str = "Operator or automated recovery") -> None:
        """Recovers from FAULT or MISSION_COMPLETE back to STANDBY."""
        if self.can_transition_to(PayloadState.STANDBY):
            self.transition_to(PayloadState.STANDBY, reason)
        elif self._current_state == PayloadState.FAULT:
            self.transition_to(PayloadState.STANDBY, reason)
        else:
            raise StateTransitionError(f"Cannot reset to STANDBY from state [{self._current_state.value}]")

    def _record_transition(
        self,
        from_state: Optional[PayloadState],
        to_state: PayloadState,
        reason: Optional[str]
    ) -> None:
        self._transition_history.append({
            "timestamp_utc": time.time(),
            "from_state": from_state.value if from_state else None,
            "to_state": to_state.value,
            "reason": reason,
        })

    def get_history(self) -> List[dict]:
        """Returns chronological list of all state transitions."""
        return list(self._transition_history)
