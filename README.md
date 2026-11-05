# Build a Small Industrial Data Historian in Python

Store synthetic tank temperature readings in SQLite. Compare source time with collector receive time and calculate a coverage aware time weighted mean. The full lesson and derivation are on [WIFA Robotics](https://wifarobotics.com/learn/industrial-data-historian/).

## Start from zero

1. Install [Python 3.10 or newer](https://www.python.org/downloads/). There are no external Python packages.
2. Choose **Code → Download ZIP** on GitHub. Extract it and open a terminal in the folder containing `historian.py`.
3. Run `python3 --version`. On Windows use `py -3` wherever a command says `python3`.

Use a new database filename for a fresh trial. The input files are read only.

```text
python3 historian.py ingest trial.sqlite examples/initial.csv
python3 historian.py summary trial.sqlite tank.temp 2026-01-01T00:00:00Z 2026-01-01T00:00:25Z --max-hold 6
python3 historian.py ingest trial.sqlite examples/late.csv
python3 historian.py summary trial.sqlite tank.temp 2026-01-01T00:00:00Z 2026-01-01T00:00:25Z --max-hold 6
python3 historian.py ingest trial.sqlite examples/late.csv
python3 historian.py ingest trial.sqlite examples/conflict.csv
python3 -m unittest discover -s tests -v
```

The initial ingest inserts 5 rows. The first report has 13 good seconds, 4 bad, 4 uncertain and 4 unknown, with coverage 0.52 and mean about 27.69 °C. The late batch inserts one row. The second report has 17 good seconds, 4 bad, 4 uncertain and 0 unknown, with coverage 0.68 and mean 28 °C. The replay reports 0 inserted and 1 duplicate. The conflict command deliberately exits with code 2; neither row from that batch is added. The tests include a separate second by second calculation over 100 reproducible windows.

## Input and output

The CSV header is `tag,source_utc,received_utc,quality,value`. Use UTC in `YYYY-MM-DDTHH:MM:SSZ`. This model accepts one reading per tag and source second. Use a finite number for `GOOD`, an empty value for `BAD` or `UNCERTAIN`, and consistent engineering units for each tag. The first stored receipt is kept for equivalent retries. A conflicting value or quality fails the batch. Reports use currently stored history and can change when delayed data arrives.

Each reading covers its source time through the earlier of the next reading and the hold limit. Reports exclude their end instant. The mean uses good seconds only; coverage is good seconds divided by total seconds. A mean of `null` means no good time, while zero can be a legitimate measured value.

## Troubleshooting

- **Could not open the database:** ingest first, then use exactly the same database filename and working directory. Summary opens read only.
- **The first mean is already 28:** the late row is already stored. Start with a different database filename.
- **Conflicting value or quality:** check units, clock precision and upstream identity. The lab never replaces the older row automatically.
- **Coverage looks low:** inspect time gaps and quality before increasing the hold limit. Compare against the expected rate of updates.
- **Expected header:** use plain UTF-8 CSV with the exact header and no byte order mark.

The editable SVG diagrams and WebP versions are in `diagrams/`. All data is synthetic. This project has no PLC connection, OPC UA client or server, production retention controls, replay at a past receive cutoff, or device validation. It implements a deliberately small aggregation policy and makes no claim of conformance to an OPC UA aggregate.

Primary references: [OPC UA DataValue](https://reference.opcfoundation.org/specs/OPC-10000-4/7.11), [Historical Access timestamps](https://reference.opcfoundation.org/specs/OPC-10000-11/4.5), [Aggregate handling](https://reference.opcfoundation.org/specs/OPC-10000-13/5.4.3), and [Python sqlite3](https://docs.python.org/3.12/library/sqlite3.html).
