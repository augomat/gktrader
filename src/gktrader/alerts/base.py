from __future__ import annotations

from abc import ABC, abstractmethod

from gktrader.domain.contracts import AlertPayload


class AlertRenderer(ABC):
    @abstractmethod
    def render(self, *args, **kwargs) -> AlertPayload:
        raise NotImplementedError


class AlertOutboxSender(ABC):
    @abstractmethod
    def send(self, payload: AlertPayload) -> dict:
        raise NotImplementedError
