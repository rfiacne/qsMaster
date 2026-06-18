import sys
from pathlib import Path

# 确保 src 目录在 Python 路径中
src_path = str(Path(__file__).resolve().parent.parent / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)
