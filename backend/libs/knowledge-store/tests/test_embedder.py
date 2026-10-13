"""The offline embedder is deterministic and 1024 dimensions.

The fastembed class is not loaded here. Tests must pass without a download.
"""

from knowledge_store.embedder import (
    DIMENSIONS,
    FASTEMBED_ID,
    FASTEMBED_MODEL,
    HASH_MODEL,
    PASSAGE_PREFIX,
    QUERY_PREFIX,
    FastEmbedEmbedder,
    HashEmbedder,
    build_embedder,
)


def test_hash_embedder_is_stable() -> None:
    embedder = build_embedder("hash")
    assert isinstance(embedder, HashEmbedder)
    assert embedder.name == HASH_MODEL
    first = embedder.embed(["parental leave"])
    second = embedder.embed(["parental leave"])
    assert first == second
    assert len(first[0]) == DIMENSIONS
    assert embedder.embed(["other"]) != first


def test_hash_embedder_is_not_semantic_and_query_matches_passage() -> None:
    embedder = HashEmbedder()
    assert embedder.semantic is False
    assert embedder.embed_query("parental leave") == embedder.embed(["parental leave"])[0]


def test_unknown_embedder_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown embedder"):
        build_embedder("openai")


def test_e5_prefixes_queries_and_passages_without_loading_weights() -> None:
    class Recording(FastEmbedEmbedder):
        def __init__(self) -> None:
            self.seen: list[str] = []

        def _run(self, texts: list[str]) -> list[list[float]]:
            self.seen.extend(texts)
            return [[0.0] * DIMENSIONS for _ in texts]

    embedder = Recording()
    assert embedder.name == FASTEMBED_MODEL
    assert embedder.semantic is True
    assert FASTEMBED_ID == "intfloat/multilingual-e5-large"
    assert DIMENSIONS == 1024
    passages = embedder.embed(["Germany leave"])
    query = embedder.embed_query("parental leave")
    assert passages[0] == [0.0] * DIMENSIONS
    assert len(query) == DIMENSIONS
    assert embedder.seen == [
        PASSAGE_PREFIX + "Germany leave",
        QUERY_PREFIX + "parental leave",
    ]


def test_bge_small_is_not_a_selectable_embedder() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown embedder"):
        build_embedder("fastembed-bge-small-v1")
