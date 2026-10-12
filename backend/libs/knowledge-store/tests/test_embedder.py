"""The offline embedder is deterministic and 384 dimensions."""

from knowledge_store.embedder import DIMENSIONS, HASH_MODEL, HashEmbedder, build_embedder


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
