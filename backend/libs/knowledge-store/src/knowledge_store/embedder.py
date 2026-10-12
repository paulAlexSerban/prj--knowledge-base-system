"""One active embedder per process. Vectors are 384 dimensions.

hash-v1 is deterministic and needs no download. It does not put similar
sentences near each other; lexical search still finds exact terms. It is the
default for tests and offline runs. The fastembed model (bge-small, ONNX, CPU)
is the semantic path and is selected with EMBEDDER=fastembed; the images bake
the model file in at build time. Mixing the two in one index is an ADR 0003
rebuild (`make reindex`), so config picks one name and every query filters to it.
"""

import hashlib
from typing import Protocol

DIMENSIONS = 384
HASH_MODEL = "hash-v1"
FASTEMBED_MODEL = "fastembed-bge-small-v1"


# bge models embed a query with this instruction and a passage without it.
BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "
FASTEMBED_ID = "BAAI/bge-small-en-v1.5"


class Embedder(Protocol):
    name: str
    semantic: bool

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Passage vectors, used at ingestion."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """One query vector. Asymmetric models add an instruction here."""
        ...


class HashEmbedder:
    name = HASH_MODEL
    semantic = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [_hash_vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _hash_vector(text)


class FastEmbedEmbedder:
    """Local bge-small through fastembed. The image build downloads the model file."""

    name = FASTEMBED_MODEL
    semantic = True

    def __init__(self) -> None:
        from fastembed import TextEmbedding

        self._model = TextEmbedding(FASTEMBED_ID)

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._run(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._run([BGE_QUERY_PREFIX + text])[0]

    def _run(self, texts: list[str]) -> list[list[float]]:
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
