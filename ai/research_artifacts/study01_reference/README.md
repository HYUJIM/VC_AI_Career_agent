# Study 01 reference inputs (for studies 02 and 03)

Read-only copies of the study-01 bundle and run artifacts, so studies 02 and 03 can be re-run from the repository alone.
Pass this folder as `--source` to `study02_prepare.py`, `study02_evaluate.py`, `study03_prepare.py`, `study03_evaluate.py`.

| File | Use |
|---|---|
| `bundle.json` | Synthetic world, signed-VC verification output, 60 case drafts. Byte-identical to `ai/outputs/paper_study/manual-pilot-v1/prepared/bundle.json` (sha256 a7e26afc…) |
| `test.pending.lock.json` | Study-01 test inputs. Study 02 checks that its U/V inputs equal study-01 B/C |
| `exploratory-run/results/*.json` | Study-01 answers (144). Study 02 checks reproduction against them |
| `live_verification.json` | Run-time re-verification of all 240 signed credentials (study 03). Used by tests; `study03_prepare.py` regenerates it when missing |

Synthetic data, agent-authored, human review pending. Files are marked `-text` in `ai/.gitattributes` so their bytes stay exact.
