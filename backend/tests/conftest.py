"""测试公共环境：在任何 src 模块导入前固定为 SQLite 内存库 + 关闭 seed。

只影响 pytest 进程；生产/容器仍按 .env 的 MySQL 运行。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ["DB_KIND"] = "sqlite"
os.environ["SEED_DEMO"] = "false"
os.environ["REDIS_URL"] = "redis://127.0.0.1:6399/0"

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
