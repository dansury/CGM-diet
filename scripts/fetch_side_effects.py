"""Download the side-effect reference used by `src/meds/side_effects.py`.

    python -m scripts.fetch_side_effects [--force]

Source: ChSe-Decagon_monopharmacy (SNAP BioSNAP, Stanford), ~10 MB gzipped.
Without it the bot falls back to the committed sample — everything works, the
reference is just smaller. The file is not committed: it is data, not code.
"""

from __future__ import annotations

import argparse
import sys
import urllib.request

from src.meds.side_effects import DATASET_PATH, DATASET_URL, dataset_status, reset_cache

CHUNK = 1 << 16


def fetch(*, force: bool = False, url: str = DATASET_URL) -> int:
    if DATASET_PATH.exists() and not force:
        print(f"{DATASET_PATH} уже на месте (--force чтобы перекачать)")
        return 0
    DATASET_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DATASET_PATH.with_suffix(DATASET_PATH.suffix + ".tmp")
    print(f"скачиваю {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as out:
            while chunk := response.read(CHUNK):
                out.write(chunk)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        print(f"не получилось: {exc}", file=sys.stderr)
        return 1
    tmp.replace(DATASET_PATH)  # atomic: a half-written dataset must never be read
    reset_cache()
    status = dataset_status()
    print(f"готово: {status.rows} записей по {status.drugs} препаратам → {status.path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="перекачать поверх существующего")
    parser.add_argument("--url", default=DATASET_URL)
    args = parser.parse_args()
    return fetch(force=args.force, url=args.url)


if __name__ == "__main__":
    raise SystemExit(main())
