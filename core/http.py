"""Shared ``aiohttp`` session factory with a bundled CA list.

Python's default SSL context on Windows reads the system certificate
stores, and those often carry stale intermediates — e.g. the old
"ISRG Root X2 cross-signed by X1" that expired 2025-09-15. OpenSSL
prefers the stored copy over the fresh one the server sends, so any
site on the newer Let's Encrypt chain fails with "certificate has
expired" (seen with api.perplexity.ai). ``certifi`` ships only roots,
so chain building uses what the server presents.
"""

from __future__ import annotations

import ssl
from functools import lru_cache

import aiohttp
import certifi


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def client_session(**kwargs) -> aiohttp.ClientSession:
    """``aiohttp.ClientSession`` that verifies against ``certifi``."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=ssl_context()), **kwargs
    )
