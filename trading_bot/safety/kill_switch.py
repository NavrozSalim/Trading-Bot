"""Emergency controls. Fail closed: any trip blocks new entries."""

from __future__ import annotations

from enum import Enum
from threading import Lock

from trading_bot.exceptions import KillSwitchActive
from trading_bot.monitoring.logger import get_logger

log = get_logger("trading_bot.kill_switch")


class KillSwitchState(str, Enum):
    CLEAR = "clear"
    PAUSE_NEW_TRADES = "pause_new_trades"
    STOP_BOT = "stop_bot"
    EMERGENCY_STOP = "emergency_stop"


class KillSwitch:
    """Process-wide kill switch. New entries are blocked unless state is CLEAR."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._state = KillSwitchState.CLEAR
        self._reason = ""
        self._close_all_requested = False

    @property
    def state(self) -> KillSwitchState:
        with self._lock:
            return self._state

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def close_all_requested(self) -> bool:
        with self._lock:
            return self._close_all_requested

    def is_clear(self) -> bool:
        return self.state is KillSwitchState.CLEAR

    def can_open_new_trades(self) -> bool:
        return self.state is KillSwitchState.CLEAR

    def should_stop_loop(self) -> bool:
        return self.state in {KillSwitchState.STOP_BOT, KillSwitchState.EMERGENCY_STOP}

    def assert_can_open_new_trades(self) -> None:
        if not self.can_open_new_trades():
            raise KillSwitchActive(
                f"Kill switch is {self.state.value}: {self.reason or 'no reason given'}"
            )

    def pause_new_trades(self, reason: str) -> None:
        self._set(KillSwitchState.PAUSE_NEW_TRADES, reason)

    def stop_bot(self, reason: str) -> None:
        self._set(KillSwitchState.STOP_BOT, reason)

    def emergency_stop(self, reason: str) -> None:
        """Stop entries, pending submissions, and automated position modifications."""
        with self._lock:
            self._state = KillSwitchState.EMERGENCY_STOP
            self._reason = reason
            self._close_all_requested = True
        log.error("kill_switch_emergency", reason=reason)

    def request_close_all_bot_positions(self, reason: str) -> None:
        with self._lock:
            self._close_all_requested = True
            if self._state is KillSwitchState.CLEAR:
                self._state = KillSwitchState.PAUSE_NEW_TRADES
                self._reason = reason
        log.warning("close_all_requested", reason=reason)

    def acknowledge_close_all(self) -> None:
        with self._lock:
            self._close_all_requested = False

    def resume(self, reason: str = "operator_resume") -> None:
        """Clear pause. Does not override EMERGENCY_STOP — use reset_emergency()."""
        with self._lock:
            if self._state is KillSwitchState.EMERGENCY_STOP:
                log.error("resume_blocked_emergency_stop")
                return
            if self._state is KillSwitchState.STOP_BOT:
                log.error("resume_blocked_stop_bot")
                return
            self._state = KillSwitchState.CLEAR
            self._reason = reason
            self._close_all_requested = False
        log.info("kill_switch_resumed", reason=reason)

    def reset_emergency(self, reason: str) -> None:
        """Explicit operator reset after an emergency. Never automatic."""
        with self._lock:
            self._state = KillSwitchState.CLEAR
            self._reason = reason
            self._close_all_requested = False
        log.warning("kill_switch_emergency_reset", reason=reason)

    def _set(self, state: KillSwitchState, reason: str) -> None:
        with self._lock:
            # Emergency always wins; do not downgrade it.
            if self._state is KillSwitchState.EMERGENCY_STOP and state != KillSwitchState.EMERGENCY_STOP:
                return
            self._state = state
            self._reason = reason
        log.warning("kill_switch_set", state=state.value, reason=reason)
