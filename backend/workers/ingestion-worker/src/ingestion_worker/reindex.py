"""Rebuild the index under the active EMBEDDER. Run with `make reindex`.

Writes chunks for the active embedding model, then removes the other models'
chunks. Safe to run twice: a second run finds nothing missing and removes nothing.
"""

import json
import sys

from knowledge_store.runtime import open_store


def main() -> int:
    """Rebuild under EMBEDDER, then drop other models. Exit 1 if anything is still missing."""
    store = open_store()
    before = store.chunks.count_by_model()
    result = store.reindex()
    result["active_model"] = store.embedder.name
    print(json.dumps({"before": before, "after": store.chunks.count_by_model(), **result}))
    return 1 if result["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
