"""Add or remove a reminder for the owner (shown in the app under "Waiting on you").

  python scripts/remind.py add review-ra-021 "Review the Systems view" --detail "..." --link RA-021 --group Reviews
  python scripts/remind.py remove review-ra-021
  python scripts/remind.py list

Use this whenever work needs the owner's input. Remove the item once the answer
is recorded in the repository (DECISIONS.md, VISION_QUESTIONS.md or evidence).
"""
import argparse, json, re
from datetime import datetime, timezone
from pathlib import Path

FILE = Path(__file__).resolve().parents[1] / "STATE" / "owner-reminders.json"


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except OSError:
        return {"schemaVersion": 1, "reminders": []}


def save(data):
    FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add"); a.add_argument("id"); a.add_argument("title")
    a.add_argument("--detail", default=""); a.add_argument("--link"); a.add_argument("--group")
    r = sub.add_parser("remove"); r.add_argument("id")
    sub.add_parser("list")
    args = parser.parse_args()
    data = load()
    if args.command == "add":
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,80}", args.id):
            parser.error("id must be lowercase letters, digits and hyphens")
        if any(item["id"] == args.id for item in data["reminders"]):
            parser.error(f"{args.id} already exists")
        data["reminders"].append({"id": args.id, "title": args.title, "detail": args.detail, "link": args.link,
                                  "group": args.group, "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")})
        save(data)
    elif args.command == "remove":
        before = len(data["reminders"])
        data["reminders"] = [item for item in data["reminders"] if item["id"] != args.id]
        if len(data["reminders"]) == before:
            parser.error(f"{args.id} not found")
        save(data)
    for item in data["reminders"]:
        print(f"{item['id']:<24} {item.get('link') or '':<8} {item['title']}")


if __name__ == "__main__":
    main()
