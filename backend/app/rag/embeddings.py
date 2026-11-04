"""Local, pinned multilingual E5 embeddings for retrieval."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Sequence

MODEL_NAME = "intfloat/multilingual-e5-small"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
EMBEDDING_DIMENSION = 384
MAX_INPUT_TOKENS = 512
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "


@lru_cache(maxsize=1)
def _load_model() -> Any:
    """Load one shared model instance on first production use."""
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(MODEL_NAME, revision=MODEL_REVISION)


class MultilingualE5Embedder:
    """Token counting and normalized embeddings backed by multilingual-e5-small.

    Tests may inject a SentenceTransformer-compatible model and tokenizer so they
    never load model files or access the network.
    """

    def __init__(self, model: Any | None = None, tokenizer: Any | None = None) -> None:
        if (model is None) != (tokenizer is None):
            raise ValueError("model and tokenizer must be injected together")
        self._model = model if model is not None else _load_model()
        self._tokenizer = tokenizer if tokenizer is not None else self._model.tokenizer

    @property
    def model_id(self) -> str:
        """Persist this full identifier so changed weights require reindexing."""
        return f"{MODEL_NAME}@{MODEL_REVISION}"

    def count_tokens(self, full_input: str) -> int:
        """Count all tokens including special tokens; never truncate silently."""
        if not isinstance(full_input, str):
            raise TypeError("embedding input must be str")
        encoded = self._tokenizer(
            full_input,
            add_special_tokens=True,
            truncation=False,
        )
        ids = encoded["input_ids"] if isinstance(encoded, dict) else encoded.input_ids
        if ids and isinstance(ids[0], (list, tuple)):
            ids = ids[0]
        return len(ids)

    def _validate(self, full_input: str, prefix: str) -> None:
        if not full_input.startswith(prefix):
            raise ValueError(f"embedding input must start with {prefix!r}")
        count = self.count_tokens(full_input)
        if count > MAX_INPUT_TOKENS:
            raise ValueError(f"embedding input has {count} tokens; maximum is {MAX_INPUT_TOKENS}")

    def _encode(self, full_inputs: Sequence[str]) -> list[list[float]]:
        if not full_inputs:
            return []
        vectors = self._model.encode(
            list(full_inputs),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        result: list[list[float]] = []
        for vector in vectors:
            values = [float(value) for value in vector]
            if len(values) != EMBEDDING_DIMENSION:
                raise ValueError(f"embedding model returned {len(values)} dimensions; expected {EMBEDDING_DIMENSION}")
            norm = sum(value * value for value in values) ** 0.5
            if not 0.999 <= norm <= 1.001:
                raise ValueError("embedding model returned a non-normalized vector")
            result.append(values)
        if len(result) != len(full_inputs):
            raise ValueError("embedding model returned the wrong number of vectors")
        return result

    def embed_passages(self, full_inputs: Sequence[str]) -> list[list[float]]:
        """Embed full ``passage: ...`` inputs, matching chunker token counts."""
        for full_input in full_inputs:
            self._validate(full_input, PASSAGE_PREFIX)
        return self._encode(full_inputs)

    def embed_query(self, text: str) -> list[float]:
        """Embed raw query text with the E5-required query prefix."""
        full_input = text if text.startswith(QUERY_PREFIX) else QUERY_PREFIX + text
        self._validate(full_input, QUERY_PREFIX)
        return self._encode([full_input])[0]


@lru_cache(maxsize=1)
def get_embedder() -> MultilingualE5Embedder:
    """Return the process-wide embedder (and therefore one model instance)."""
    return MultilingualE5Embedder()
