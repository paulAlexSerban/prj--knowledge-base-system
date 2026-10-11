"""Write the JSON Schema generated from SourceContract."""

import json
from pathlib import Path

from shared.contracts.source_contract import SourceContract

SCHEMA_PATH = Path(__file__).with_name("source_contract.schema.json")


def render_schema() -> str:
    schema = SourceContract.model_json_schema()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMA_PATH.write_text(render_schema())


if __name__ == "__main__":
    main()
