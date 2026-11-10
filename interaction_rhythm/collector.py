"""Platform-neutral runtime contract for interaction collection adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class CollectorAdapter(ABC):
    """Lifecycle boundary between the application and a platform collector."""

    @abstractmethod
    def start(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def flush(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def checkpoint(self) -> None:
        raise NotImplementedError

    @property
    @abstractmethod
    def stats(self) -> Any:
        raise NotImplementedError
