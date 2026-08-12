"""One logger that works both inside StreamController and in a bare test run.

StreamController provides loguru; a plain pytest process does not necessarily,
and importing it unconditionally would make the core modules untestable.
"""

from __future__ import annotations

import logging

try:  # pragma: no cover - depends on the host environment
    from loguru import logger as log
except ImportError:  # pragma: no cover
    log = logging.getLogger("spotify_essentials")
    if not log.handlers:
        log.addHandler(logging.NullHandler())

_DEBUG_ENABLED = False


def set_debug_logging(enabled: bool) -> None:
    """Plugin-wide debug switch, off by default.

    Only affects this plugin's own verbosity — StreamController's level is left
    alone. No token, authorization code or verifier is ever passed to `debug`.
    """
    global _DEBUG_ENABLED
    _DEBUG_ENABLED = bool(enabled)


def debug(message: str) -> None:
    if _DEBUG_ENABLED:
        log.debug(message)
