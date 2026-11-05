import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.data.query_loader import load_queries
if __name__ == "__main__":
    print(f"Validated {len(load_queries(sys.argv[1]))} queries")
