import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent

src_path = project_root / 'src'
sys.path.insert(0, str(src_path))

sys.path.insert(0, str(project_root))


def pytest_sessionfinish(session, exitstatus):
    """Stop the exit hook logging into a stream pytest has already closed"""
    import atexit

    from src.utils import shutdown

    if shutdown._shutdown_manager is not None:
        atexit.unregister(shutdown._shutdown_manager._cleanup)
