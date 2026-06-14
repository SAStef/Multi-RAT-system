#!/usr/bin/env python3
"""
T3 — Uafhængigt pakketab.

Taber ~30% af pakkerne uafhængigt på BEGGE paths.

PASS: hver path viser ~30% loss, men MERGED loss ~9% (0.3 x 0.3) — altså
      markant lavere end hver enkelt path. Bekræfter uafhængigheds-gevinsten
      (P_loss = P1 x P2).

Kør:  python3 test_t3_loss.py            (lokalt)
      python3 test_t3_loss.py --ip <server-ip>
"""
import multirat_testlib as lib

lib.run(
    title="T3 Uafhaengigt pakketab (30% paa begge paths)",
    ip=lib.parse_ip(),
    loss=(0.30, 0.30),
)
