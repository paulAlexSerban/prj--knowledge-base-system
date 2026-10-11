"""One active embedder per process. Vectors are 384 dimensions.

hash-v1 is deterministic and needs no download. It does not put similar
sentences near each other; lexical search still finds exact terms. The
fastembed model is the quality path and is selected with EMBEDDER=fastembed
when that package is installed. Mixing the two in one index is an ADR 0003
rebuild, so config picks one name and every query filters to it.
"""

import hashlib
from typing import Protocol

DIMENSIONS = 384
HASH_MODEL = "hash-v1"
FASTEMBED_MODEL = "fastembed-bge-small-v1"


class Embedder(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class HashEmbedder:
    name = HASH_MODEL

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]


class FastEmbedEmbedder:
    """Local bge-small. The model file is downloaded on first use, not baked in."""

    name = FASTEMBED_MODEL

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed(self, texts: list[str]) -> list[list[float]]:
        rows = []
        for row in self._model.embed(texts):
            values = [float(item) for item in row]
            if len(values) != DIMENSIONS:
                raise ValueError(f"expected {DIMENSIONS} dimensions, got {len(values)}")
            rows.append(values)
        return rows


def build_embedder(name: str) -> Embedder:
    if name in {"hash", HASH_MODEL}:
        return HashEmbedder()
    if name in {"fastembed", FASTEMBED_MODEL}:
        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder {name}")


def _hash_vector(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    values: list[float] = []
    while len(values) < DIMENSIONS:
        seed = hashlib.sha256(seed).digest()
        for index in range(0, len(seed), 2):
            raw = int.from_bytes(seed[index : index + 2], "big")
            values.append((raw / 65535.0) * 2 - 1)
    values = values[:DIMENSIONS]
    norm = sum(value * value for value in values) ** 0.5 or 1.0
    return [value / norm for value in values]
