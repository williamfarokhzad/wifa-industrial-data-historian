import csv
import math
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from historian import FIELDS, SCHEMA, ingest, parse_row, summarize, utc_seconds

ROOT = Path(__file__).resolve().parents[1]
START = "2026-01-01T00:00:00Z"
END = "2026-01-01T00:00:25Z"
ZERO = utc_seconds(START)


class HistorianTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute(SCHEMA)

    def test_late_data_and_idempotent_replay(self):
        self.assertEqual(ingest(self.db, ROOT / "examples/initial.csv"), {"inserted": 5, "duplicates": 0})
        before = summarize(self.db, "tank.temp", ZERO, ZERO + 25, 6)
        self.assertEqual(before, dict(window_seconds=25, good_seconds=13, bad_seconds=4,
                                     uncertain_seconds=4, unknown_seconds=4, coverage=13/25,
                                     time_weighted_mean=360/13))
        ingest(self.db, ROOT / "examples/late.csv")
        after = summarize(self.db, "tank.temp", ZERO, ZERO + 25, 6)
        self.assertEqual(after["good_seconds"], 17)
        self.assertEqual(after["unknown_seconds"], 0)
        self.assertAlmostEqual(after["time_weighted_mean"], 476/17)
        self.assertEqual(ingest(self.db, ROOT / "examples/late.csv"), {"inserted": 0, "duplicates": 1})
        self.assertEqual(summarize(self.db, "tank.temp", ZERO, ZERO + 25, 6), after)

    def test_conflict_rolls_back_preceding_insert(self):
        ingest(self.db, ROOT / "examples/late.csv")
        with self.assertRaisesRegex(ValueError, "line 3.*Conflicting"):
            ingest(self.db, ROOT / "examples/conflict.csv")
        self.assertEqual(self.db.execute("SELECT source_s,received_s,value FROM samples").fetchall(),
                         [(ZERO + 4, ZERO + 23, 26.0)])

    def test_retry_preserves_first_stored_receipt(self):
        ingest(self.db, ROOT / "examples/late.csv")
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "retry.csv"
            with file.open("w", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(FIELDS)
                writer.writerow(["tank.temp", "2026-01-01T00:00:04Z", "2026-01-01T00:00:27Z", "GOOD", "26"])
            self.assertEqual(ingest(self.db, file)["duplicates"], 1)
        self.assertEqual(self.db.execute("SELECT received_s FROM samples").fetchone(), (ZERO + 23,))

    def test_boundary_and_end_exclusion(self):
        self.db.executemany("INSERT INTO samples VALUES (?, ?, ?, ?, ?)", [
            ("x", 10, 11, "GOOD", 6), ("x", 20, 21, "BAD", None),
            ("y", 14, 15, "GOOD", 99)])
        result = summarize(self.db, "x", 14, 20, 6)
        self.assertEqual((result["good_seconds"], result["bad_seconds"], result["unknown_seconds"]), (2, 0, 4))
        self.assertEqual(result["time_weighted_mean"], 6)
        self.assertIsNone(summarize(self.db, "y", 2, 14, 6)["time_weighted_mean"])

    def test_random_windows_against_integer_second_oracle(self):
        rng = random.Random(470)
        for _ in range(100):
            self.db.execute("DELETE FROM samples")
            points = [(t, q, rng.randint(-40, 40) if q == "GOOD" else None)
                      for t in sorted(rng.sample(range(-10, 40), 12))
                      for q in [rng.choice(["GOOD", "BAD", "UNCERTAIN"])]]
            self.db.executemany("INSERT INTO samples VALUES ('tag', ?, 99, ?, ?)", reversed(points))
            start = rng.randrange(-12, 37)
            end = rng.randrange(start + 1, 45)
            hold = rng.randrange(1, 11)
            expected = {q: 0 for q in ("GOOD", "BAD", "UNCERTAIN", "UNKNOWN")}
            good_values = []
            for second in range(start, end):
                eligible = [(t, q, value) for t, q, value in points if second - hold < t <= second]
                _, quality, value = max(eligible) if eligible else (None, "UNKNOWN", None)
                expected[quality] += 1
                if quality == "GOOD":
                    good_values.append(value)
            got = summarize(self.db, "tag", start, end, hold)
            self.assertEqual([got[q.lower() + "_seconds"] for q in expected], list(expected.values()))
            self.assertEqual(got["coverage"], len(good_values) / (end - start))
            if good_values:
                self.assertTrue(math.isclose(got["time_weighted_mean"], sum(good_values) / len(good_values)))
            else:
                self.assertIsNone(got["time_weighted_mean"])

    def test_reject_malformed_or_unsafe_fields(self):
        for row in (["tag", START[:-1], END, "GOOD", "1"], ["tag", START, END, "GOOD", "nan"],
                    ["tag", START, END, "GOOD", "inf"], ["tag", START, END, "BAD", "0"],
                    ["tag", START, END, "UNKNOWN", ""], ["bad tag", START, END, "GOOD", "0"]):
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse_row(row)
        with self.assertRaises(ValueError):
            summarize(self.db, "x", 5, 4, 6)
        with self.assertRaises(ValueError):
            summarize(self.db, "x", 4, 5, 0)

    def test_missing_database_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "missing db.sqlite"
            result = subprocess.run([sys.executable, str(ROOT / "historian.py"), "summary",
                                     str(file), "tank.temp", START, END, "--max-hold", "6"],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn("unable to open database", result.stderr)
            self.assertFalse(file.exists())


if __name__ == "__main__":
    unittest.main()
