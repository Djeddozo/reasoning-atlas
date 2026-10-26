"""HQ adapter boundary. Read-only: snapshots are imported (adapters/hq/snapshot.py); nothing is written to HQ."""
import sqlite3
from contextlib import closing


def status(db_path=None):
    """Adapter status for /api/health. Without a database path, report the synthetic boundary only."""
    if db_path is None:
        return {"mode": "synthetic", "connected": False, "capabilities": [], "lastSuccessfulSync": None}
    from services.atlas import enginesync as connect
    with closing(sqlite3.connect(db_path, timeout=10)) as db:
        link = connect.connection(db)
    return {"mode": "snapshot", "connected": link["state"] == "connected", "state": link["state"],
            "capabilities": ["readSnapshot"] if link["snapshotId"] else [],
            "lastSuccessfulSync": link["lastSyncAt"], "upstreamCommit": link["sourceCommit"]}
