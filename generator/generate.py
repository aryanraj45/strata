#!/usr/bin/env python3
"""Generate the synthetic dataset.

    python generator/generate.py --out data --volume 900 --days 14 --seed 42
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from strata_gen import World, write_all


def main() -> int:
    ap = argparse.ArgumentParser(description="STRATA synthetic dataset generator")
    ap.add_argument("--out", type=Path, default=Path("data"), help="output directory")
    ap.add_argument("--volume", type=int, default=900, help="approximate baseline transaction count")
    ap.add_argument("--days", type=int, default=14, help="days of activity to simulate")
    ap.add_argument("--seed", type=int, default=42, help="RNG seed; same seed, same dataset")
    ap.add_argument(
        "--coverage",
        type=float,
        default=0.70,
        help="probability a sensor is a direct peer of the originating host",
    )
    args = ap.parse_args()

    rng = random.Random(args.seed)
    world = World(rng, coverage=args.coverage)
    world.run(days=args.days, volume=args.volume)
    stats = write_all(world, args.out)

    truth = world.truth
    print(f"transactions   {stats['transactions']:>7,}")
    print(f"observations   {stats['records']:>7,}")
    print(f"entities       {len(world.actors):>7,}")
    print(f"peeling chains {len(truth['peeling_chains']):>7,}")
    print(f"coinjoins      {len(truth['coinjoins']):>7,}")
    print(f"cash-outs      {len(truth['cashouts']):>7,}")
    print(f"\nwritten to {args.out.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
