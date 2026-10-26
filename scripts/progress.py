"""Report build progress to the running Atlas app.

Agents (Codex, Claude) and people call this while working on a task. The app
shows it as a progress bar in its header.

  python scripts/progress.py start RA-004 "Visual shell" "Fix layout" "Build client" "Tests" "Checkpoint"
  python scripts/progress.py step 2
  python scripts/progress.py step 2 --message "Rebuilding client" --progress 40
  python scripts/progress.py done "RA-004 checkpoint saved"
  python scripts/progress.py fail "Tests failing"
  python scripts/progress.py show
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.atlas import progress  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("start"); s.add_argument("task"); s.add_argument("title"); s.add_argument("steps", nargs="+")
    s = sub.add_parser("step"); s.add_argument("index", type=int); s.add_argument("--message"); s.add_argument("--progress", type=int)
    s = sub.add_parser("done"); s.add_argument("message", nargs="?", default="Done")
    s = sub.add_parser("fail"); s.add_argument("message")
    sub.add_parser("show")
    args = parser.parse_args()
    if args.command == "start":
        data = progress.start(args.task, args.title, args.steps)
    elif args.command == "step":
        data = progress.step(args.index, args.message, args.progress)
    elif args.command == "done":
        data = progress.finish(args.message)
    elif args.command == "fail":
        data = progress.fail(args.message)
    else:
        data = progress.read()
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
