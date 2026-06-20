#!/usr/bin/env python3
from pathlib import Path
import csv

DATA_DIR = Path("data")
PATTERN = "trustpilot_search-*.csv"
OUT_FILE = DATA_DIR / "merged.csv"

files = sorted(DATA_DIR.glob(PATTERN))
if not files:
    print("No files found matching pattern", PATTERN)
    raise SystemExit(1)

# Collect union of fieldnames in order of appearance
fieldnames = []
for p in files:
    with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames:
            for n in reader.fieldnames:
                if n not in fieldnames:
                    fieldnames.append(n)

# Add source_file column if missing
if "source_file" not in fieldnames:
    fieldnames.append("source_file")

with OUT_FILE.open("w", encoding="utf-8", newline="") as outfh:
    writer = csv.DictWriter(outfh, fieldnames=fieldnames)
    writer.writeheader()
    total_rows = 0
    for p in files:
        with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                row.setdefault("source_file", p.name)
                out_row = {k: (row.get(k, "") if row.get(k) is not None else "") for k in fieldnames}
                writer.writerow(out_row)
                total_rows += 1

print(f"Merged {len(files)} files into {OUT_FILE} ({total_rows} rows)")
