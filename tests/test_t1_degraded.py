#!/usr/bin/env python3
"""
T1 — Degraded path / survivability (HOVEDTEST).

Wi-Fi (path 1) slukkes midt i kørslen mens 5G (path 2) fortsætter.
Faser: 15s begge -> 15s kun 5G -> derefter begge igen (til du trykker q).

PASS: path 1 dør, path 2 kører videre, og MERGED loss forbliver 0% hele vejen.

Kør:  python3 test_t1_degraded.py            (lokalt)
      python3 test_t1_degraded.py --ip <server-ip>
"""
import multirat_testlib as lib

_args = lib.cli()
lib.run(
    title="T1 Degraded path / survivability",
    ip=_args.ip,
    duration=_args.seconds,
    phases=[
        (15, {1: True, 2: True}),     # begge paths
        (15, {1: False, 2: True}),    # Wi-Fi slukket -> kun 5G
        (None, {1: True, 2: True}),   # begge igen, indtil q
    ],
)
