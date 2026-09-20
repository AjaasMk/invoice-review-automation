from typing import Protocol

from pipeline.schemas import IncomingDocument


class IntakeSource(Protocol):
    def poll(self) -> list[IncomingDocument]: ...
