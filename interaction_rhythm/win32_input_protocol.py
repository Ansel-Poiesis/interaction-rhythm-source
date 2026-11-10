"""Private marker protocol for synthetic Win32 input used by benchmarks."""
from __future__ import annotations

import ctypes


BENCHMARK_PROTOCOL_VERSION = 2

_BENCHMARK_PREFIX = 0x49520000
_BENCHMARK_MASK = 0xFFFF0000
_SEQUENCE_MASK = 0x0000FFFF

_LEGACY_64_PREFIX = 0x4952420000000000
_LEGACY_64_MASK = 0xFFFFFF0000000000
_LEGACY_64_SEQUENCE_MASK = 0x000000FFFFFFFFFF


def benchmark_extra_info(sequence: int) -> int:
    """Encode a non-secret benchmark sequence in ``dwExtraInfo``."""
    return _BENCHMARK_PREFIX | (int(sequence) & _SEQUENCE_MASK)


def benchmark_sequence(value: int) -> int | None:
    """Return the sequence for a marked event, otherwise ``None``."""
    normalized = int(value or 0)
    if normalized & _BENCHMARK_MASK == _BENCHMARK_PREFIX:
        return normalized & _SEQUENCE_MASK
    if (
        ctypes.sizeof(ctypes.c_void_p) == 8
        and normalized & _LEGACY_64_MASK == _LEGACY_64_PREFIX
    ):
        return normalized & _LEGACY_64_SEQUENCE_MASK
    return None


def is_benchmark_input(value: int) -> bool:
    return benchmark_sequence(value) is not None
