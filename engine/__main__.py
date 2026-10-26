import sys
from .cli import main

def _report_crash(kind, exc, tb):
    from .recovery import capture
    if sys.argv[1:2] in (["serve"], ["bot"]) and not issubclass(kind, (KeyboardInterrupt, SystemExit)):
        capture(exc)
    sys.__excepthook__(kind, exc, tb)

sys.excepthook = _report_crash
main()
