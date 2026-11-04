"""No model dependency: a deterministic token counter exercises boundaries."""

import unittest

from app.rag.chunking import chunk_text


class ChunkingTests(unittest.TestCase):
    @staticmethod
    def count_characters(full_embedding_input: str) -> int:
        return len(full_embedding_input)

    def test_exact_code_point_offsets_and_heading_titles(self) -> None:
        source = "Intro 😀.\n\n# Claims\nUse ₹99.\n\nMention warranty.\n\n# Tone\nBe warm."
        chunks = chunk_text(source, count_tokens=self.count_characters, max_tokens=27)
        self.assertEqual([chunk.title for chunk in chunks], ["Untitled", "Claims", "Claims", "Tone"])
        self.assertEqual([chunk.chunk_index for chunk in chunks], list(range(len(chunks))))
        for chunk in chunks:
            self.assertEqual(source[chunk.start_offset:chunk.end_offset], chunk.text)
            self.assertLessEqual(self.count_characters("passage: " + chunk.text), 27)
        self.assertEqual(chunks[0].end_offset, len("Intro 😀."))

    def test_long_paragraph_splits_at_sentences_then_words(self) -> None:
        source = "First sentence. Second sentence. Third sentence."
        chunks = chunk_text(source, count_tokens=self.count_characters, max_tokens=27)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertIn("First sentence.", [chunk.text for chunk in chunks])
        self.assertTrue(all(source[c.start_offset:c.end_offset] == c.text for c in chunks))

    def test_single_long_word_is_split_without_truncation(self) -> None:
        source = "abcdefghij"
        chunks = chunk_text(source, count_tokens=self.count_characters, max_tokens=12)
        self.assertEqual("".join(chunk.text for chunk in chunks), source)
        self.assertTrue(all(len("passage: " + chunk.text) <= 12 for chunk in chunks))

    def test_invalid_normalization_and_empty_content(self) -> None:
        with self.assertRaises(ValueError):
            chunk_text("e\u0301", count_tokens=self.count_characters)
        with self.assertRaises(ValueError):
            chunk_text("A\r\nB", count_tokens=self.count_characters)
        with self.assertRaises(ValueError):
            chunk_text("# Heading only", count_tokens=self.count_characters)


if __name__ == "__main__":
    unittest.main()
