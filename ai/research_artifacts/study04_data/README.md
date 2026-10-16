# Study 04 dataset (~1,000 pairs)

The exact data used in study 04. Pass this folder as `--source` to `study03_prepare.py` and `study03_evaluate.py`.

| File | Content |
|---|---|
| `world.json` | 44 synthetic applicants (dev 2, test 42), same templates as study 01 |
| `bundle.json` | World + signed-VC verification output (incl. public DID/trust/revocation snapshots) + 264 case drafts |
| `dummy_records.json` | Plain-document twin of every credential (test 1,008 + dev 48): same title and text, no VC fields |
| `data_report.json` | Counts and verification-status totals |

Regenerating with `scripts/study04_build_data.py` gives the same facts but new signing keys and record IDs
(private keys are never stored), so model answers can differ slightly. Use this folder for an exact re-run.

Synthetic data, agent-authored, gold checked automatically only; human review pending.
