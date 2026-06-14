#!/usr/bin/env python3
"""
T5 — Korruption / CRC paa path 1.

Oedelaegger payloaden paa ~30% af pakkerne paa path 1, saa CRC fejler hos
receiveren.

PASS: path 1's crc_errors-taeller stiger, de oedelagte pakker droppes, og
      MERGED er uberoert (path 2's kopi er intakt). Korrupte pakker taelles
      som crc_errors, IKKE som loss.

Kør:  python3 test_t5_corrupt.py            (lokalt)
      python3 test_t5_corrupt.py --ip <server-ip>
"""
import multirat_testlib as lib

lib.run(
    title="T5 Korruption / CRC paa path 1 (30%)",
    ip=lib.parse_ip(),
    corrupt=(0.30, 0.0),
)
