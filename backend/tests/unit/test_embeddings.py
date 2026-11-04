"""Offline tests for the local multilingual E5 adapter."""

import unittest

from app.rag.embeddings import (
    EMBEDDING_DIMENSION,
    MAX_INPUT_TOKENS,
    MultilingualE5Embedder,
)


class FakeTokenizer:
    def __call__(self, text, *, add_special_tokens, truncation):
        assert add_special_tokens is True
        assert truncation is False
        # One token per character plus two model special tokens.
        return {"input_ids": [0, *range(len(text)), 1]}


class FakeModel:
    tokenizer = FakeTokenizer()

    def __init__(self):
        self.inputs = None
        self.options = None

    def encode(self, inputs, **kwargs):
        self.inputs = inputs
        self.options = kwargs
        vector = [1.0] + [0.0] * (EMBEDDING_DIMENSION - 1)
        return [vector for _ in inputs]


class MultilingualE5EmbedderTests(unittest.TestCase):
    def setUp(self):
        self.model = FakeModel()
        self.embedder = MultilingualE5Embedder(self.model, self.model.tokenizer)

    def test_counts_full_input_including_special_tokens(self):
        self.assertEqual(self.embedder.count_tokens("passage: abc"), len("passage: abc") + 2)

    def test_passages_require_prefix_and_normalize(self):
        vectors = self.embedder.embed_passages(["passage: hello", "passage: नमस्ते"])
        self.assertEqual(len(vectors), 2)
        self.assertEqual(len(vectors[0]), EMBEDDING_DIMENSION)
        self.assertEqual(self.model.inputs, ["passage: hello", "passage: नमस्ते"])
        self.assertTrue(self.model.options["normalize_embeddings"])
        self.assertAlmostEqual(sum(value * value for value in vectors[0]) ** 0.5, 1.0)
        with self.assertRaisesRegex(ValueError, "must start"):
            self.embedder.embed_passages(["hello"])

    def test_query_gets_query_prefix(self):
        vector = self.embedder.embed_query("hello")
        self.assertEqual(len(vector), EMBEDDING_DIMENSION)
        self.assertEqual(self.model.inputs, ["query: hello"])

    def test_accepts_exactly_512_tokens_and_rejects_longer_without_truncation(self):
        # Fake tokenizer adds 2 special tokens, so the literal full input is 510 chars.
        accepted = "passage: " + "x" * (MAX_INPUT_TOKENS - 2 - len("passage: "))
        self.assertEqual(self.embedder.count_tokens(accepted), MAX_INPUT_TOKENS)
        self.embedder.embed_passages([accepted])
        with self.assertRaisesRegex(ValueError, "maximum is 512"):
            self.embedder.embed_passages([accepted + "x"])

    def test_requires_model_and_tokenizer_pair(self):
        with self.assertRaisesRegex(ValueError, "injected together"):
            MultilingualE5Embedder(model=self.model)


if __name__ == "__main__":
    unittest.main()
