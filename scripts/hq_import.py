"""Import an HQ snapshot into the Atlas read model (read-only towards HQ).

  python scripts/hq_import.py fixtures/hq-snapshot-1599d42.json
  python scripts/hq_import.py snapshot.json --db runtime/atlas.db --check   # validate only, write nothing

Running the same snapshot twice reports everything as unchanged.
"""
import argparse, json, sqlite3, sys
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from adapters.hq import snapshot  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("file")
    parser.add_argument("--db", default=str(ROOT / "runtime" / "atlas.db"))
    parser.add_argument("--check", action="store_true", help="validate and normalize only")
    args = parser.parse_args()
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    try:
        if args.check:
            result = snapshot.normalize(data)
            print(json.dumps({"valid": True, "records": len(result["records"]), "tombstones": len(result["tombstones"]),
                              "excluded": result["excluded"]}, indent=2))
            return
        Path(args.db).parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(args.db, timeout=10)) as db:
            print(json.dumps(snapshot.import_snapshot(db, data), indent=2))
    except snapshot.IncompatibleSnapshot as exc:
        sys.exit(f"Incompatible snapshot: {exc}")
    except ValueError as exc:
        sys.exit(f"Invalid snapshot: {exc}")


if __name__ == "__main__":
    main()
