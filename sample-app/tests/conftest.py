import sys
import os
from pathlib import Path

project_root = Path(__file__).parent.parent

src_path = project_root / 'src'
sys.path.insert(0, str(src_path))

sys.path.insert(0, str(project_root))