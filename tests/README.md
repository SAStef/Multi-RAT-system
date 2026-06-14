# Multi-RAT — tests

Test-sendere der koerer mod receiveren + dashboardet. Hver fil er en selvstaendig
test. Test-senderen erstatter telefonen som afsender, saa du behoever ikke appen.

## Saadan koerer du

1. **Terminal 1** — start server + receiver (i `src/WebAppExpress`):
   ```
   node server.js
   ```
   Aabn dashboardet i browseren.

2. **Terminal 2** — koer en test (i denne mappe, `tests/`):
   ```
   python3 test_t1_degraded.py
   ```
   Koerer lokalt (127.0.0.1) som standard. Mod en server: `--ip <server-ip>`.

3. Lad testen koere ~30-60 sek saa graferne fyldes, tag screenshot, og tryk
   **q** + Enter (eller Ctrl-C) for at stoppe.

Under en koersel kan du styre manuelt: **1** = toggle Wi-Fi, **2** = toggle 5G,
**q** = quit (tryk Enter efter).

## Filer

| Fil | Test | PASS |
|-----|------|------|
| `test_t1_degraded.py` | path 1 doer midt i koerslen | merged loss = 0% hele vejen |
| `test_t2_baseline.py` | rent, ingen fejl | 0% loss, 0 CRC-fejl, dedup 2:1 |
| `test_t3_loss.py` | 30% tab paa begge paths | merged ~9% (0.3 x 0.3) |
| `test_t4_reorder.py` | reordering paa path 1 | ingen falsk loss, merged 0% |
| `test_t5_corrupt.py` | 30% korruption paa path 1 | crc_errors stiger, merged uberoert |
| `multirat_testlib.py` | faelles afsender-logik | koeres ikke direkte |

Alle koerer med fast seed, saa resultaterne er reproducerbare.
