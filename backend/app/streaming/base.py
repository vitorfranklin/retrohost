"""Video/audio streaming interface from the emulator to the browser."""
from abc import ABC, abstractmethod
from typing import Any


class StreamingProvider(ABC):
    @abstractmethod
    def start(self, game: Any) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> bool:
        raise NotImplementedError
