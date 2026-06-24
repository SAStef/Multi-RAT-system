# Multi-RAT — tests

Test-sendere der koerer mod den 24/7 cloud-server. Hver fil er en selvstaendig
test. Test-senderen erstatter telefonen som afsender, saa du behoever ikke appen.

Server + receiver koerer hele tiden paa cloud-serveren (`34.32.45.194`, via pm2),
saa du starter dem aldrig selv — du sender bare en test derhen og henter CSV'erne
hjem. Wrapper-scripsene har serveren hardcodet (IP, bruger og SSH-noegle).

## Saadan koerer du

Foerst, eengang pr. terminal-session (saa du slipper passphrase ved hvert SSH/SCP-trin):
```
ssh-add ~/.ssh/id_ed25519
```

**Python-test som afsender** — one-shot: sender, henter CSV, laver alle plots:
```
./run_test.sh test_t3_loss.py 60 --fresh
```
Skift testen ud med `test_t1_degraded.py` / `t2` / `t4` / `t5`. `--fresh` genstarter
receiveren paa serveren foerst, saa CSV'en kun indeholder netop den test.

**Telefonen som afsender** — ingen lokal python-sender:
```
./fetch_and_plot.sh --fresh      # FOER app'en starter (rydder CSV paa serveren)
# ... koer app'en paa telefonen, stop den ...
./fetch_and_plot.sh              # henter + laver alle plots
```

Plots + raa CSV-data ender i `src/analysis/figures/run_<timestamp>/`.

Vil du styre en koersel manuelt, sender testene ogsaa direkte: `1` = toggle Wi-Fi,
`2` = toggle 5G, `q` = quit (tryk Enter efter), eller koer en test selv mod
serveren med `python3 test_t1_degraded.py --ip 34.32.45.194 --seconds 60` og hent
bagefter med `./fetch_and_plot.sh` (uden `--fresh`).

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
