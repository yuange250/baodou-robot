"""项目根目录与静态资源路径（src 布局下统一由此解析）。"""

from __future__ import annotations

import os
from pathlib import Path

# service/ 项目根目录（含 config.yaml、data/、models/）
# deskbot_server/utils/paths.py → parents[3] == service/
_runtime_root = (os.environ.get("DESKBOT_PROJECT_ROOT") or "").strip()
# Keep an explicitly supplied subst/junction path unresolved. Some native
# Windows ML libraries cannot reopen non-ASCII paths after resolving them.
PROJECT_ROOT = (
    Path(os.path.abspath(os.path.expanduser(_runtime_root)))
    if _runtime_root
    else Path(__file__).resolve().parents[3]
)

DATA_DIR = PROJECT_ROOT / "data"
PROMPTS_DIR = PROJECT_ROOT / "prompts"
MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
ENV_FILE = PROJECT_ROOT / ".env"
