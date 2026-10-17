"""旧脚本书写方式的兼容入口，正式入口为 oconnect-client。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from oconnect_client.cli import main  # noqa: E402

if __name__ == "__main__":
    main()
