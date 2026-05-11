"""In-memory FIFO cache of finished transcriptions.

Lets the bot offer "give me another format/summary of the same file" without
re-downloading the source or re-running GigaAM. Cleared on bot restart, which
is fine — the user can always send the file again.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from typing import List

from .transcriber import Segment


_MAX_ENTRIES = 50

_cache: "OrderedDict[str, List[Segment]]" = OrderedDict()


def register(segments: List[Segment]) -> str:
    """Store segments and return a short id for use in callback_data."""
    short_id = uuid.uuid4().hex[:10]
    _cache[short_id] = segments

    while len(_cache) > _MAX_ENTRIES:
        _cache.popitem(last=False)

    return short_id


def get(short_id: str) -> List[Segment] | None:
    return _cache.get(short_id)
