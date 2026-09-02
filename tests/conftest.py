"""Test-only import bootstrap for repository operator scripts.

The application package remains installed from ``profile_agent``. Repository
maintenance CLIs live under ``scripts/`` and are intentionally not part of the
runtime package, so tests that exercise those CLIs add that directory to the
module search path during collection.
"""

from pathlib import Path
import sys


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
scripts_path = str(SCRIPTS_DIR)
if scripts_path not in sys.path:
    sys.path.insert(0, scripts_path)
