"""Single source of the app version.

Bumped by hand for every release; the tag on GitHub must match
(``v1.0.3`` ↔ ``1.0.3``) because :mod:`core.updater` compares them.
"""

from __future__ import annotations

__version__ = "1.0.3"
