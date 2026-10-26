"""Resume, claim, checkpoint and guard external effects from the repository alone (RA-015).

A fresh Claude or Codex session runs `show` first. See services/atlas/continuity.py.

  python scripts/resume.py show [--json]
  python scripts/resume.py claim RA-015 --owner Claude --session s1
  python scripts/resume.py checkpoint --next "Write the evidence file" [--note "..."] [--status review]
  python scripts/resume.py effect begin push:RA-015 --task RA-015 --kind push --target origin/feat/ra-015
  python scripts/resume.py effect finish push:RA-015 done|failed|not_done [--detail "..."]

`effect begin` prints run, skip or verify and exits 0, 3 or 4, so a script can
write: python scripts/resume.py effect begin KEY ... && git push && python scripts/resume.py effect finish KEY done
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.atlas import continuity  # noqa: E402

EXIT = {"run": 0, "skip": 3, "verify": 4}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(continuity.ROOT), help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("show"); s.add_argument("--json", action="store_true")
    s = sub.add_parser("claim"); s.add_argument("task"); s.add_argument("--owner", required=True); s.add_argument("--session", required=True)
    s = sub.add_parser("checkpoint"); s.add_argument("--next", required=True); s.add_argument("--note"); s.add_argument("--status")
    s = sub.add_parser("effect"); e = s.add_subparsers(dest="step", required=True)
    b = e.add_parser("begin"); b.add_argument("key"); b.add_argument("--task"); b.add_argument("--kind", required=True); b.add_argument("--target", required=True)
    f = e.add_parser("finish"); f.add_argument("key"); f.add_argument("outcome", choices=continuity.OUTCOMES); f.add_argument("--detail", default="")
    args = parser.parse_args(argv)
    root = Path(args.root)
    try:
        if args.command == "show":
            state = continuity.resume(root)
            print(json.dumps(state, indent=2) if args.json else continuity.text(state))
        elif args.command == "claim":
            print(json.dumps(continuity.claim(args.task, args.owner, args.session, root), indent=2))
        elif args.command == "checkpoint":
            print(json.dumps(continuity.checkpoint(args.next, root, args.note, args.status), indent=2))
            print("Now commit (and push) so this becomes the checkpoint.", file=sys.stderr)
        elif args.step == "begin":
            task = args.task or (continuity.resume(root)["taskId"])
            action, entry = continuity.begin(args.key, task, args.kind, args.target, root)
            print(action if action == "run" else f"{action}: {args.key} is {entry['state']} since {entry.get('finishedAt') or entry.get('intendedAt')}")
            return EXIT[action]
        else:
            print(json.dumps(continuity.finish(args.key, args.outcome, root, args.detail), indent=2))
    except ValueError as error:
        print(error, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
