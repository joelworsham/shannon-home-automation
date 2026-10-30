"""Daily local-time blocks; gaps deliberately have no temperature intent."""
from dataclasses import dataclass, replace
from datetime import time, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from climate_policy import PolicyConfig, Decision, FanState, evaluate


def parse_clock(value):
    if not isinstance(value, str) or len(value) != 5 or value[2] != ':':
        raise ValueError('Block times must be HH:MM')
    try:
        result = time.fromisoformat(value)
    except ValueError:
        raise ValueError('Block times must be HH:MM') from None
    if result.tzinfo is not None or result.second or result.microsecond:
        raise ValueError('Block times must be HH:MM')
    return result


@dataclass(frozen=True)
class ScheduleBlock:
    name: str
    start: time
    end: time
    target_f: float
    restart_f: float
    high_at_f: float | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 80:
            raise ValueError('Block names must contain 1–80 characters')
        if any(ord(c) < 32 for c in self.name):
            raise ValueError('Block names cannot contain control characters')
        for value in (self.start, self.end):
            if not isinstance(value, time) or value.tzinfo or value.second or value.microsecond:
                raise ValueError('Block times must be local HH:MM')
        for value in (self.target_f, self.restart_f):
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ValueError('Block temperatures must be numbers')
        if self.high_at_f is not None and (isinstance(self.high_at_f, bool) or not isinstance(self.high_at_f, (float, int))):
            raise ValueError('Block temperatures must be numbers')
        # Reuse the cooling policy's finite-number and threshold validation.
        self.policy(PolicyConfig())

    def policy(self, base):
        return replace(base, schedule_start=self.start, schedule_end=self.end,
                       target_f=self.target_f, restart_f=self.restart_f, high_at_f=self.high_at_f)

    def as_dict(self):
        return dict(name=self.name, start=f'{self.start:%H:%M}', end=f'{self.end:%H:%M}',
                    target_f=self.target_f, restart_f=self.restart_f, high_at_f=self.high_at_f)

    def contains(self, local):
        clock = local.time().replace(tzinfo=None)
        if self.start < self.end:
            return self.start <= clock < self.end
        return clock >= self.start or clock < self.end

    def occurrence(self, local):
        day = local.date()
        if self.start > self.end and local.time().replace(tzinfo=None) < self.end:
            day -= timedelta(days=1)
        return f'{day.isoformat()}/{self.name}'


def load_blocks(path):
    try:
        raw = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        raise ValueError('Cannot read valid JSON from CLIMATE_SCHEDULE_FILE') from None
    if not isinstance(raw, list):
        raise ValueError('Schedule must be a JSON list of blocks')
    blocks = []
    for item in raw:
        required = {'name', 'start', 'end', 'target_f', 'restart_f'}
        if not isinstance(item, dict) or not required <= item.keys() or item.keys() - required - {'high_at_f'}:
            raise ValueError('Each block needs name, start, end, target_f, restart_f; optional high_at_f')
        blocks.append(ScheduleBlock(**dict(item, start=parse_clock(item['start']), end=parse_clock(item['end']))))
    validate_blocks(blocks)
    return tuple(blocks)


def validate_blocks(blocks):
    names, occupied = set(), set()
    for block in blocks:
        if block.name in names:
            raise ValueError('Schedule block names must be unique')
        names.add(block.name)
        start = block.start.hour * 60 + block.start.minute
        end = block.end.hour * 60 + block.end.minute
        minutes = set(range(start, end)) if start < end else set(range(start, 1440)) | set(range(end))
        if minutes & occupied:
            raise ValueError('Schedule blocks must not overlap, including across midnight')
        occupied.update(minutes)


def select_block(when, blocks, timezone):
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError('Schedule evaluation requires timezone-aware time')
    local = when.astimezone(ZoneInfo(timezone))
    return next((b for b in blocks if b.contains(local)), None)


def block_decision(block, *, when, bedroom_f, outdoor_f, previous, base):
    effective = block.policy(base)
    result = evaluate(when=when, bedroom_f=bedroom_f, outdoor_f=outdoor_f,
                      previous=previous, config=effective)
    return replace(result, active_block=block.name, automation_mode='temperature', effective_policy=effective)


def gap_decision(shutdown=False):
    return Decision(FanState() if shutdown else None,
                    'Block ended; guarded shutdown pending' if shutdown else 'No active block; manual fan operation allowed',
                    False, 'schedule_shutdown' if shutdown else 'automation_inactive',
                    automation_mode='shutdown_pending' if shutdown else 'inactive')
