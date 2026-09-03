"""Windows build preflight — keep logic out of cmd.exe python -c strings."""
from __future__ import annotations

import sys
import sysconfig


def main() -> None:
    if sys.version_info < (3, 10):
        raise SystemExit("need Python 3.10+")
    exe = sys.executable.lower()
    if "windowsapps" in exe:
        raise SystemExit("refuse Windows Store python stub: " + sys.executable)
    plat = sysconfig.get_platform().lower()
    if plat != "win-amd64":
        raise SystemExit(f"need win-amd64 platform, got {sysconfig.get_platform()}")
    print(sys.version)
    print(sys.executable)
    print("platform=" + sysconfig.get_platform())


if __name__ == "__main__":
    main()
