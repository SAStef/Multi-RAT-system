#!/usr/bin/env python3
"""
T2 — Baseline / korrekthed (reference).

Begge paths kører rent uden fejl-injektion.

PASS: 0% loss og 0 CRC-fejl på begge paths, dedup-forhold 2:1
      (antal duplicates ~= antal unikke), merged jitter <= path jitter.

Kør:  python3 test_t2_baseline.py            (lokalt)
      python3 test_t2_baseline.py --ip <server-ip>
"""
import multirat_testlib as lib

lib.run(
    title="T2 Baseline / korrekthed",
    ip=lib.parse_ip(),
)
