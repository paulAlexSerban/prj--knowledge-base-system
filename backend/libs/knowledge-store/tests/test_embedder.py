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
