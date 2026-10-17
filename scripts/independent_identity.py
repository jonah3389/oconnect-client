"""兼容旧的 identity init/show 命令。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from oconnect_client.identity import main  # noqa: E402

if __name__ == "__main__":
    main()
