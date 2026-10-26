import unittest
from pathlib import Path

from engine import db, priority, skilltree


class WholeQueue(unittest.TestCase):
    def test_the_queue_is_no_longer_cut_at_60(self):
        """#181: build_order took only the top 60 ranked AI tasks, so after his queue, locked and waiting ones
        about 35 were left in view; now every open AI task is in the build order."""
        con = db.connect(Path(":memory:"))
        db.init(con)
        for n in range(90):
            db.insert(con, "tasks", title=f"Builder task {n}", status="todo", owner="ai", impact=1 + n % 5, effort_h=1)
        con.commit()
        plan = skilltree.build_plan(con)
        self.assertEqual(len(plan["proposed"]), 90)
        skilltree.set_lane(con, plan["proposed"][-1]["id"], 1)
        plan = skilltree.build_plan(con)
        self.assertEqual((len(plan["lane"]), len(plan["proposed"])), (1, 89))
        self.assertEqual(len(priority.next_tasks(con, None, owner="ai")), 90)
        self.assertEqual(len(priority.next_tasks(con, 5)), 5)


if __name__ == "__main__":
    unittest.main()
