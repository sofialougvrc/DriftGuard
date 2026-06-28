from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

from .models import EnvironmentFingerprint


def _read_first_existing(paths: list[Path]) -> str | None:
    for path in paths:
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return None


def _load_average_1m() -> float | None:
    try:
        return os.getloadavg()[0]
    except (AttributeError, OSError):
        return None


def _cpu_governor() -> str | None:
    return _read_first_existing(
        [
            Path("/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor"),
            Path("/sys/devices/system/cpu/cpufreq/policy0/scaling_governor"),
        ]
    )


def _perf_event_paranoid() -> int | None:
    value = _read_first_existing([Path("/proc/sys/kernel/perf_event_paranoid")])
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def capture_environment() -> EnvironmentFingerprint:
    cpu_count = os.cpu_count()
    load_1m = _load_average_1m()
    governor = _cpu_governor()
    perf_paranoid = _perf_event_paranoid()
    warnings: list[str] = []

    if load_1m is not None and cpu_count and load_1m > cpu_count * 0.75:
        warnings.append(f"1-minute load average {load_1m:.2f} is high for {cpu_count} CPUs")
    if governor and governor not in {"performance"}:
        warnings.append(f"CPU governor is {governor!r}; performance governor is preferred")
    if perf_paranoid is not None and perf_paranoid > 2:
        warnings.append(f"perf_event_paranoid={perf_paranoid}; hardware counters may be unavailable")

    return EnvironmentFingerprint(
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor(),
        python_version=sys.version.split()[0],
        cpu_count=cpu_count,
        load_average_1m=load_1m,
        cpu_governor=governor,
        perf_event_paranoid=perf_paranoid,
        warnings=warnings,
    )
