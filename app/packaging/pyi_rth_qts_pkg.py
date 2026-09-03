# Ensure frozen app can import quant_trading_system from _MEIPASS.
import sys

if getattr(sys, "frozen", False):
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        if meipass not in sys.path:
            sys.path.insert(0, meipass)
