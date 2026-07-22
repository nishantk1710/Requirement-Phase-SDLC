"""P6 review API (FastAPI). `create_app(repo)` builds the ASGI app around an
already-initialised Repository; `rga serve` wires it to the configured store and runs it."""

from .app import create_app

__all__ = ["create_app"]
