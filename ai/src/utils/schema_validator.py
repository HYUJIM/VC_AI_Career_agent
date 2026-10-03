import json
import rfc3339_validator  # Ensure date-time validation cannot silently be skipped.
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_DIR = Path(__file__).resolve().parents[3] / "VC" / "src" / "schemas"

def validate(value, name, schema_dir=SCHEMA_DIR):
    schema = json.loads((Path(schema_dir) / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
