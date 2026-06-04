"""Make the repo root importable so ``import service...`` works under any
``unittest discover`` invocation (including ``-s service/tests``).

Each test module imports this first. It walks up from this file to the repo
root (the parent of the ``service`` package) and puts it on ``sys.path``.
"""

import os
import sys

# .../service/tests/_bootstrap.py -> repo root is three levels up.
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir)
)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
