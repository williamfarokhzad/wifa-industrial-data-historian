"""A local SQLite historian for second-resolution synthetic readings."""
import argparse
from contextlib import closing
import csv
from datetime import datetime, timezone
import json
import math
import re
import sqlite3

FORMAT = "%Y-%m-%dT%H:%M:%SZ"
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FIELDS = ["tag", "source_utc", "received_utc", "quality", "value"]
SCHEMA = """CREATE TABLE IF NOT EXISTS samples (
    tag TEXT NOT NULL, source_s INTEGER NOT NULL, received_s INTEGER NOT NULL,
    quality TEXT NOT NULL CHECK (quality IN ('GOOD', 'BAD', 'UNCERTAIN')),
    value REAL, PRIMARY KEY (tag, source_s),
    CHECK ((quality = 'GOOD' AND value IS NOT NULL)
           OR (quality != 'GOOD' AND value IS NULL))
) WITHOUT ROWID"""


def utc_seconds(text):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", text):
        raise ValueError("Use UTC timestamps like 2026-01-01T00:00:00Z")
    delta = datetime.strptime(text, FORMAT).replace(tzinfo=timezone.utc) - EPOCH
    return delta.days * 86400 + delta.seconds


def parse_row(row):
    if len(row) != 5:
        raise ValueError("Expected five CSV fields")
    tag, source, received, quality, raw = row
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", tag):
        raise ValueError("Use a 1–64 character tag with letters, digits, dot, underscore or hyphen")
    if quality not in ("GOOD", "BAD", "UNCERTAIN"):
        raise ValueError("Quality must be GOOD, BAD or UNCERTAIN")
    if quality == "GOOD":
        value = float(raw)
        if not math.isfinite(value) or abs(value) > 1e12:
            raise ValueError("GOOD value must be finite and at most 1e12 in magnitude")
    else:
        if raw:
            raise ValueError("Leave value empty for BAD or UNCERTAIN")
        value = None
    return tag, utc_seconds(source), utc_seconds(received), quality, value


def ingest(db, path):
    """Load a batch in one transaction; retain the first stored receipt on replay."""
    inserted = duplicates = 0
    with open(path, encoding="utf-8", newline="") as stream, db:
        rows = csv.reader(stream, strict=True)
        if next(rows, None) != FIELDS:
            raise ValueError("Expected header: " + ",".join(FIELDS))
        db.execute("BEGIN IMMEDIATE")
        for row in rows:
            try:
                tag, source, received, quality, value = parse_row(row)
                cursor = db.execute(
                    "INSERT INTO samples VALUES (?, ?, ?, ?, ?) ON CONFLICT(tag,source_s) DO NOTHING",
                    (tag, source, received, quality, value),
                )
                if cursor.rowcount:
                    inserted += 1
                elif db.execute(
                    "SELECT quality,value FROM samples WHERE tag=? AND source_s=?", (tag, source)
                ).fetchone() == (quality, value):
                    duplicates += 1
                else:
                    raise ValueError(f"Conflicting value or quality for {tag} at {row[1]}")
            except ValueError as exc:
                raise ValueError(f"CSV line {rows.line_num}: {exc}") from exc
    return {"inserted": inserted, "duplicates": duplicates}


def summarize(db, tag, start, end, max_hold):
    if not start < end or max_hold <= 0:
        raise ValueError("End must follow start and max-hold must be positive")
    # Read the boundary sample and window rows from the same SQLite snapshot.
    rows = db.execute(
        """SELECT source_s,quality,value FROM samples
           WHERE tag=? AND source_s >= COALESCE(
             (SELECT max(source_s) FROM samples WHERE tag=? AND source_s<=?), ?)
           AND source_s<? ORDER BY source_s""",
        (tag, tag, start, start, end),
    ).fetchall()
    durations = dict(good_seconds=0, bad_seconds=0, uncertain_seconds=0)
    terms = []
    for i, (source, quality, value) in enumerate(rows):
        next_source = rows[i + 1][0] if i + 1 < len(rows) else end
        seconds = max(0, min(next_source, end, source + max_hold) - max(source, start))
        durations[quality.lower() + "_seconds"] += seconds
        if quality == "GOOD":
            terms.append(value * seconds)
    window = end - start
    good = durations["good_seconds"]
    return {
        "window_seconds": window, **durations,
        "unknown_seconds": window - sum(durations.values()),
        "coverage": good / window,
        "time_weighted_mean": math.fsum(terms) / good if good else None,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    add = commands.add_parser("ingest")
    add.add_argument("db")
    add.add_argument("csv")
    read = commands.add_parser("summary")
    for name in ("db", "tag", "start", "end"):
        read.add_argument(name)
    read.add_argument("--max-hold", type=int, required=True, metavar="SECONDS")
    args = parser.parse_args(argv)
    try:
        if args.action == "summary":
            # Open an existing database without creating it on a typo.
            from pathlib import Path
            uri = Path(args.db).resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, uri=True)) as db:
                result = summarize(db, args.tag, utc_seconds(args.start), utc_seconds(args.end), args.max_hold)
        else:
            with closing(sqlite3.connect(args.db)) as db:
                db.execute(SCHEMA)
                result = ingest(db, args.csv)
        print(json.dumps(result, allow_nan=False))
    except (OSError, ValueError, csv.Error, sqlite3.Error) as exc:
        parser.exit(2, f"historian: {exc}\n")


if __name__ == "__main__":
    main()
