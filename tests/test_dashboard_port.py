"""A second dashboard on a port that is already taken exits cleanly instead of being recorded as a crash to repair."""
import os as _os; _os.environ.setdefault("HQ_NO_CLAUDE_CODE", "1")
import socket
import unittest
from unittest import mock

from engine import dashboard


class PortInUseTest(unittest.TestCase):
    def test_port_in_use_exits_without_incident(self):
        taken = socket.socket(); taken.bind(("127.0.0.1", 0)); taken.listen(1); self.addCleanup(taken.close)
        port = taken.getsockname()[1]
        with mock.patch.object(dashboard.recovery, "capture") as capture, self.assertRaises(SystemExit) as stop:
            dashboard.serve(port)
        self.assertIn(f"Port {port} is already in use", str(stop.exception))
        capture.assert_not_called()

    def test_other_os_errors_still_surface(self):
        with mock.patch.object(dashboard, "RecoveryServer", side_effect=PermissionError(13, "denied")), self.assertRaises(PermissionError):
            dashboard.serve(1)


if __name__ == "__main__":
    unittest.main()
