"""Pluggable time-system adapters for graph-memory time ranges."""

from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .schema import HierarchicalGraphMemory

DAY_OFFSET = 100_000


def _finite_seconds(value: int | float) -> float | None:
    try:
        seconds = float(value)
    except OverflowError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


class TimeSystem(ABC):
    """Interface for converting between seconds and display strings,
    and for applying dataset-specific time offsets to a loaded memory."""

    mode_name: str

    @abstractmethod
    def sec_to_str(self, sec: float) -> str: ...

    @abstractmethod
    def str_to_sec(self, val) -> float | None: ...

    def patch_offsets(self, memory: HierarchicalGraphMemory) -> int:
        """Mutate time_ranges in-place. Return count of patched macros."""
        return 0


class DefaultTimeSystem(TimeSystem):
    mode_name = "default"

    def sec_to_str(self, sec: float) -> str:
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = int(sec % 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def str_to_sec(self, val) -> float | None:
        if isinstance(val, (int, float)):
            return _finite_seconds(val)
        if not isinstance(val, str):
            return None
        m = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d)", val.strip())
        if m:
            return _finite_seconds(float(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)))
        return None


class EgoLifeTimeSystem(TimeSystem):
    mode_name = "egolife"

    RECORDING_START_SEC: dict[int, int] = {
        1: 11 * 3600 + 9 * 60 + 42,  # DAY1_A1_JAKE_11094208
        2: 10 * 3600 + 44 * 60 + 25,  # DAY2_A1_JAKE_10442506
        3: 11 * 3600 + 17 * 60 + 27,  # DAY3_A1_JAKE_11172702
        4: 10 * 3600 + 48 * 60 + 20,  # DAY4_A1_JAKE_10482000
        5: 11 * 3600 + 0 * 60 + 31,  # DAY5_A1_JAKE_11003100
        6: 9 * 3600 + 49 * 60 + 33,  # DAY6_A1_JAKE_09493300
        7: 11 * 3600 + 56 * 60 + 8,  # DAY7_A1_JAKE_11560817
    }

    def sec_to_str(self, sec: float) -> str:
        day_idx = int(sec // DAY_OFFSET)
        remainder = sec % DAY_OFFSET
        h = int(remainder // 3600)
        m = int((remainder % 3600) // 60)
        s = int(remainder % 60)
        return f"DAY{day_idx + 1} {h:02d}:{m:02d}:{s:02d}"

    def str_to_sec(self, val) -> float | None:
        if isinstance(val, (int, float)):
            return _finite_seconds(val)
        if not isinstance(val, str):
            return None
        s = val.strip()
        # Accept the dataset's underscore spelling as well as runs of whitespace.
        m = re.fullmatch(r"day([1-9]\d*)(?:_|\s+)(\d{1,2}):([0-5]\d):([0-5]\d)", s, re.IGNORECASE)
        if m:
            day = float(m.group(1))
            if int(m.group(2)) >= 24:
                return None
            return _finite_seconds(
                (day - 1) * DAY_OFFSET + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4))
            )
        return None

    def _already_offset(self, memory: HierarchicalGraphMemory) -> bool:
        """Detect if the merge step (merge_memories.py) already applied DAY offsets."""
        for me in memory.macro_events:
            m = re.match(r"DAY(\d+)_", me.macro_id or "")
            if not m:
                continue
            day_num = int(m.group(1))
            start = self.RECORDING_START_SEC.get(day_num, 0)
            if not me.time_range or len(me.time_range) < 2:
                continue
            if isinstance(me.time_range[0], str):
                continue
            expected_base = (day_num - 1) * DAY_OFFSET + start
            if me.time_range[0] >= expected_base:
                return True
            return False
        return False

    def patch_offsets(self, memory: HierarchicalGraphMemory) -> int:
        from .toolkit import time_val_to_sec

        if self._already_offset(memory):
            return 0

        patched = 0
        for me in memory.macro_events:
            m = re.match(r"DAY(\d+)_", me.macro_id or "")
            if not m:
                continue
            day_num = int(m.group(1))
            total_offset = (day_num - 1) * DAY_OFFSET + self.RECORDING_START_SEC.get(day_num, 0)
            if total_offset == 0:
                continue
            if me.time_range and len(me.time_range) >= 2:
                if isinstance(me.time_range[0], str):
                    continue
                me.time_range = [me.time_range[0] + total_offset, me.time_range[1] + total_offset]
            if me.subgraph:
                for ev in me.subgraph.micro_events:
                    if ev.time_range and len(ev.time_range) >= 2 and not isinstance(ev.time_range[0], str):
                        ev.time_range = [ev.time_range[0] + total_offset, ev.time_range[1] + total_offset]
                for ocr in me.subgraph.on_screen_texts:
                    if ocr.time_range and len(ocr.time_range) >= 2 and not isinstance(ocr.time_range[0], str):
                        ocr.time_range = [ocr.time_range[0] + total_offset, ocr.time_range[1] + total_offset]
            patched += 1

        for se in memory.super_events:
            sub_trs = []
            for mid in se.sub_macro_ids:
                for me in memory.macro_events:
                    if me.macro_id == mid and me.time_range:
                        sub_trs.append(me.time_range)
                        break
            if sub_trs:
                se.time_range = [
                    min(time_val_to_sec(t[0]) for t in sub_trs),
                    max(time_val_to_sec(t[1]) for t in sub_trs),
                ]
        return patched


def detect_time_system(memory: HierarchicalGraphMemory) -> TimeSystem:
    if any(re.match(r"DAY\d+_", me.macro_id or "") for me in memory.macro_events):
        return EgoLifeTimeSystem()
    return DefaultTimeSystem()
