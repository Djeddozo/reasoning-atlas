"""Quick Cortex (the ✦ box, #166): Send during recording sends once the transcript is in; thoughts that cannot reach
the Engine wait in this browser and go to its drop box when it is back, never filed as a note on this computer."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = (ROOT / "apps/web/src/main.tsx").read_text(encoding="utf-8")


class QuickCortexSource(unittest.TestCase):
    def test_send_while_recording_waits_for_the_transcript_then_sends_once(self):
        self.assertIn("if(dict.listening){sendWhenHeard.current=true;dict.toggle();return}", MAIN)
        self.assertIn("if(sendWhenHeard.current&&!dict.listening&&!dict.working){sendWhenHeard.current=false;send()}", MAIN)

    def test_waiting_thoughts_go_to_the_drop_box_and_are_not_saved_on_this_computer(self):
        self.assertIn("setWaiting([...readWaiting(),x]);setKind('waiting')", MAIN)
        self.assertIn("post('engine.brain.write_thought',{text:left[0]})", MAIN)
        self.assertIn("canWrite=hubHas(hub,'engine.brain.write_thought')", MAIN)
        # an offline Engine no longer sends a plain thought to This computer
        self.assertNotIn("if(!canAsk){await keepLocal(x,false)", MAIN)

    def test_waiting_queue_is_kept_in_the_browser_and_shown(self):
        self.assertIn("const WAIT_KEY='atlas-waiting'", MAIN)
        self.assertIn("waiting for {cx}; sent when it is back.", MAIN)


if __name__ == "__main__":
    unittest.main()
