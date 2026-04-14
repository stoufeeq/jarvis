"""
Base class for all signal providers.
Add new providers by subclassing BaseSignalProvider and registering
them in SignalEngine._providers.
"""

from abc import ABC, abstractmethod

from app.models.signal import Signal


class BaseSignalProvider(ABC):
    """
    Contract: scan(ticker) -> list[Signal]

    Providers must NOT add signals to the database session — the engine does that.
    Providers return fully-populated Signal ORM objects (unsaved).
    """

    @abstractmethod
    async def scan(self, ticker: str) -> list[Signal]:
        """Return zero or more signals for the given ticker."""
        ...

    @property
    def name(self) -> str:
        return self.__class__.__name__
