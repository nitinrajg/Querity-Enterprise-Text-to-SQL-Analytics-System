"""
Synthetic Dataset Generator for Querity Ingestion Benchmarks.
Generates:
1. messy_test.csv: Edge-case CSV with mixed types, quoted commas, nulls, and unusual dates.
2. bench_100mb.csv: ~100 MB realistic transaction dataset.
3. bench_sample.parquet: PyArrow parquet dataset.
4. Optional bench_1gb.csv if explicitly requested via --generate-1gb.
"""

import os
import sys
import csv
import time
import random
import argparse
from pathlib import Path
import datetime

import pyarrow as pa
import pyarrow.parquet as pq

DATA_DIR = Path(__file__).resolve().parent / "data"


def generate_messy_csv(out_path: Path, num_rows: int = 5000):
    """
    Generates a CSV designed to stress-test the schema type inference engine:
    - col_id: integer with occasional blank
    - col_price: float with numbers, empty strings, and 'NaN'
    - col_bool: boolean mixed as True/False, 1/0, 'yes'/'no'
    - col_date: ISO timestamps mixed with date-only and empty values
    - col_text_commas: text strings with embedded commas and quotes
    - col_ambiguous_mixed: numbers for first 400 rows, then text strings!
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    headers = [
        "id_col", "price_col", "active_flag", "event_timestamp",
        "quoted_text_col", "mixed_type_col"
    ]

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        for i in range(1, num_rows + 1):
            # id
            val_id = i if i % 100 != 0 else ""

            # price
            if i % 50 == 0:
                val_price = ""
            elif i % 75 == 0:
                val_price = "nan"
            else:
                val_price = round(random.uniform(5.0, 999.99), 2)

            # bool
            val_bool = random.choice(["true", "false", "TRUE", "False", "1", "0", "yes", "no"])

            # date
            if i % 30 == 0:
                val_date = ""
            elif i % 2 == 0:
                val_date = datetime.datetime(2024, random.randint(1, 12), random.randint(1, 28), random.randint(0, 23), random.randint(0, 59)).isoformat()
            else:
                val_date = f"2024-{random.randint(1, 12):02d}-{random.randint(1, 28):02d}"

            # quoted text with commas
            val_text = f'Item #{i}, "Special Edition", with warranty'

            # mixed type (stress test type inference sampling first 500 rows)
            if i <= 400:
                val_mixed = i * 10
            else:
                val_mixed = f"string_variant_{i}"

            writer.writerow([val_id, val_price, val_bool, val_date, val_text, val_mixed])

    print(f"[generate] Created messy CSV: {out_path} ({out_path.stat().st_size / 1024:.1f} KB)", flush=True)


def generate_benchmark_csv(out_path: Path, target_mb: int = 100):
    """Generates synthetic e-commerce transaction CSV close to target_mb."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    target_bytes = target_mb * 1024 * 1024

    headers = [
        "transaction_id", "customer_id", "product_sku", "category",
        "unit_price", "quantity", "discount_pct", "total_amount",
        "payment_method", "transaction_timestamp", "is_flagged"
    ]

    categories = ["Electronics", "Home & Kitchen", "Fitness", "Books", "Fashion", "Beauty"]
    payments = ["credit_card", "debit_card", "upi", "paypal", "crypto"]

    print(f"[generate] Generating ~{target_mb}MB CSV at {out_path}...", flush=True)
    t0 = time.perf_counter()

    rows_written = 0
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)

        while True:
            batch = []
            for _ in range(5000):
                rows_written += 1
                u_price = round(random.uniform(9.99, 1499.99), 2)
                qty = random.randint(1, 10)
                disc = round(random.choice([0.0, 0.05, 0.10, 0.15, 0.20]), 2)
                total = round(u_price * qty * (1.0 - disc), 2)
                ts = datetime.datetime(2024, random.randint(1, 12), random.randint(1, 28), random.randint(0, 23), random.randint(0, 59)).isoformat()

                batch.append([
                    rows_written,
                    random.randint(1001, 99999),
                    f"SKU-{random.randint(100, 999)}-{random.choice(categories)[:3].upper()}",
                    random.choice(categories),
                    u_price,
                    qty,
                    disc,
                    total,
                    random.choice(payments),
                    ts,
                    random.choice([True, False, False, False])
                ])

            writer.writerows(batch)

            if out_path.stat().st_size >= target_bytes:
                break

    actual_mb = out_path.stat().st_size / (1024 * 1024)
    elap = time.perf_counter() - t0
    print(f"[generate] Finished {out_path.name}: {actual_mb:.2f} MB ({rows_written:,} rows in {elap:.1f}s)", flush=True)


def generate_benchmark_parquet(out_path: Path, num_rows: int = 500000):
    """Generates synthetic PyArrow Parquet dataset."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[generate] Generating Parquet file ({num_rows:,} rows) at {out_path}...", flush=True)
    t0 = time.perf_counter()

    categories = ["Electronics", "Furniture", "Fitness", "Stationery", "Footwear"]
    statuses = ["delivered", "shipped", "pending", "cancelled"]

    data = {
        "order_id": pa.array([100000 + i for i in range(num_rows)], type=pa.int64()),
        "customer_id": pa.array([random.randint(1, 50000) for _ in range(num_rows)], type=pa.int32()),
        "category": pa.array([random.choice(categories) for _ in range(num_rows)], type=pa.string()),
        "status": pa.array([random.choice(statuses) for _ in range(num_rows)], type=pa.string()),
        "amount": pa.array([round(random.uniform(10.0, 5000.0), 2) for _ in range(num_rows)], type=pa.float64()),
        "items_count": pa.array([random.randint(1, 20) for _ in range(num_rows)], type=pa.int32()),
    }

    table = pa.Table.from_pydict(data)
    pq.write_table(table, str(out_path), compression="SNAPPY")

    actual_mb = out_path.stat().st_size / (1024 * 1024)
    elap = time.perf_counter() - t0
    print(f"[generate] Finished {out_path.name}: {actual_mb:.2f} MB ({num_rows:,} rows in {elap:.1f}s)", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic benchmark datasets")
    parser.add_argument("--include-1gb", action="store_true", help="Also generate 1GB CSV (takes ~1 minute)")
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Messy test CSV
    generate_messy_csv(DATA_DIR / "messy_test.csv", num_rows=5000)

    # 2. 100 MB benchmark CSV
    generate_benchmark_csv(DATA_DIR / "bench_100mb.csv", target_mb=100)

    # 3. 500,000-row Parquet file (~15-20 MB compressed)
    generate_benchmark_parquet(DATA_DIR / "bench_sample.parquet", num_rows=500000)

    # 4. Optional 1 GB CSV
    if args.include_1gb:
        generate_benchmark_csv(DATA_DIR / "bench_1gb.csv", target_mb=1000)
