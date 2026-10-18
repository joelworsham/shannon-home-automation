"""Minimal, mockable hardware boundaries.

Never confuse a requested IR command with confirmed physical fan state.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Protocol


class IRBlaster(Protocol):
    def send(self, command: str) -> None:
        """Send a captured remote command. No acknowledgment of physical effect."""


class PowerMeter(Protocol):
    def read_watts(self) -> float | None:
        """Return instantaneous AC input power (watts), or None if unavailable."""


@dataclass
class FakeBroadLink:
    """Test double only. This class never uses network or IR hardware."""
    commands: list[str] = field(default_factory=list)

    def send(self, command: str) -> None:
        if command not in {"power_toggle", "direction_toggle", "speed_up", "speed_down"}:
            raise ValueError(f"Unsupported simulated IR command: {command}")
        self.commands.append(command)


@dataclass
class FakeShelly:
    """Power-monitoring-only test double. No outlet switching methods."""
    watts: float | None

    def read_watts(self) -> float | None:
        return self.watts
