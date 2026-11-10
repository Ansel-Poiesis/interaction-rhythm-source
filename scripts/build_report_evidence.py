"""Generate and validate a local report packet. No send or cloud integration."""
from pathlib import Path
import argparse
import json
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from interaction_rhythm.report_evidence import build_evidence, validate_evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--period", choices=["weekly", "monthly"], default="weekly")
    parser.add_argument("--timezone", default="Asia/Shanghai")
    parser.add_argument("--existing-snapshot", action="store_true")
    parser.add_argument("--settings", type=Path, help="read only hidden_apps; no credentials copied")
    args = parser.parse_args()
    source, out = args.db.resolve(), args.out_dir.resolve()
    if not source.is_file():
        parser.error("database does not exist")
    if out == source.parent:
        parser.error("report output must be separate from the database directory")
    out.mkdir(parents=True, exist_ok=True)
    snapshot = source if args.existing_snapshot else out / "private-snapshot.db"
    if not args.existing_snapshot:
        if snapshot == source:
            parser.error("snapshot cannot overwrite source")
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as src:
            src.execute("PRAGMA query_only=ON")
            with sqlite3.connect(snapshot) as dst:
                src.backup(dst)
    hidden = []
    settings = args.settings or source.parent / "settings.json"
    if settings.exists():
        raw = json.loads(settings.read_text(encoding="utf-8-sig")).get("hidden_apps", [])
        hidden = raw.split(",") if isinstance(raw, str) else raw
    packet = build_evidence(snapshot, args.end_date, args.period, args.timezone, hidden)
    validate_evidence(packet)
    target = out / "evidence.json"
    target.write_text(json.dumps(packet, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"validated": True, "contract": packet["contract_version"], "output": str(target), "delivery": "none"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
