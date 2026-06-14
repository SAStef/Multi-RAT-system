#!/usr/bin/env python3
"""
T4 — Reordering paa path 1.

Ombytter ~30% af pakkerne paa path 1 (intet tabes, alt ankommer i forkert
raekkefoelge).

PASS: path 1 viser IKKE falsk loss (loss ~0%), fordi receiveren bruger en
      reordering-robust tael-metode; merged loss = 0%. Path 1's latency/jitter
      stiger dog (ombyttede pakker ankommer reelt for sent) - det er forventet.

Kør:  python3 test_t4_reorder.py            (lokalt)
      python3 test_t4_reorder.py --ip <server-ip>
"""
import multirat_testlib as lib

lib.run(
    title="T4 Reordering paa path 1 (30%)",
    ip=lib.parse_ip(),
    reorder=(0.30, 0.0),
)
