"""Copy the BE<->AI contract into the BE repository (byte-identical).

Run from the AI `ai/` folder after changing src/career/contracts:

    python scripts/sync_contract_to_be.py               # ../BE in this repository
    python scripts/sync_contract_to_be.py --be <BE dir> # explicit BE folder
    python scripts/sync_contract_to_be.py --check       # CI: fail if drifted

Then run both test suites and redeploy BE and AI together (the contract ID changes).
"""

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "career" / "contracts"
DEFAULT_BE = ROOT.parent / "BE"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--be", type=Path, default=DEFAULT_BE, help="BE folder containing app/")
    parser.add_argument("--check", action="store_true", help="only report drift")
    args = parser.parse_args()
    target = args.be.resolve() / "app" / "ai_contract"
    if not (args.be / "app").is_dir():
        sys.exit(f"Not a BE folder (no app/): {args.be}")
    sys.path.insert(0, str(ROOT))
    from src.career.contracts import CONTRACT_FILES, CONTRACT_ID

    names = ["__init__.py", *CONTRACT_FILES]
    drifted = [
        name
        for name in names
        if not (target / name).exists()
        or (target / name).read_bytes() != (SOURCE / name).read_bytes()
    ]
    if args.check:
        if drifted:
            sys.exit(f"BE contract copy differs: {', '.join(drifted)}")
        print(f"BE contract copy matches {CONTRACT_ID}")
        return
    target.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(SOURCE / name, target / name)
    stale = sorted(p.name for p in target.glob("*.py") if p.name not in names)
    print(f"Synced {len(names)} files to {target} ({CONTRACT_ID})")
    if stale:
        print(f"Remove files no longer in the contract: {', '.join(stale)}")


if __name__ == "__main__":
    main()
