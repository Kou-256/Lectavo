from __future__ import annotations

from importlib import import_module


class LazyModule:
    """Defer native libraries until a processing worker needs them."""

    def __init__(self, name: str):
        self.name = name
        self.module = None

    def __getattr__(self, attribute: str):
        if self.module is None:
            self.module = import_module(self.name)
        return getattr(self.module, attribute)
