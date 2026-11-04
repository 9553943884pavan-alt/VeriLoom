"""RRF should promote agreement without double-counting one retriever."""

import unittest

from app.rag.retrieval.fusion import fuse_rankings


class FusionTests(unittest.TestCase):
    def test_agreement_beats_single_list_and_duplicates_do_not_add_score(self) -> None:
        result = fuse_rankings(["a", "b", "b", "c"], ["b", "d", "a"])
        self.assertEqual([item.passage_id for item in result], ["b", "a", "d", "c"])
        self.assertEqual(result[0].dense_rank, 2)
        self.assertEqual(result[0].lexical_rank, 1)
        self.assertAlmostEqual(result[0].score, 1 / 62 + 1 / 61)

    def test_limits_and_deterministic_tie(self) -> None:
        result = fuse_rankings(["b", "a", "c"], ["a", "b", "d"], per_list_limit=2, fused_limit=2)
        self.assertEqual([item.passage_id for item in result], ["a", "b"])

    def test_empty_and_invalid_input(self) -> None:
        self.assertEqual(fuse_rankings([], []), [])
        with self.assertRaises(ValueError):
            fuse_rankings([""], [])
        with self.assertRaises(ValueError):
            fuse_rankings(["a"], [], rrf_k=0)


if __name__ == "__main__":
    unittest.main()
