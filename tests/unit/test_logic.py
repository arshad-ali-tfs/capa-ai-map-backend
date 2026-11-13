import unittest
import numpy as np
from run_map import locate_training_rows, l2_normalize

class ModelHelpersTest(unittest.TestCase):
    def test_exact_training_rows(self):
        raw = np.array([[1,2,3],[4,5,6],[7,8,9]], dtype=np.float32)
        sample = raw[[2,0]]
        inds, approx, ambiguity = locate_training_rows(sample, raw)
        np.testing.assert_array_equal(inds, [2,0])
        self.assertFalse(approx.any())
        self.assertFalse(ambiguity.any())

    def test_unmatched_returns_minus_one(self):
        raw = np.array([[1,2,3],[4,5,6]],dtype=np.float32)
        inds, _, ambiguity = locate_training_rows(np.array([[9,8,7]],dtype=np.float32),raw)
        self.assertFalse(ambiguity.any())
        self.assertEqual(int(inds[0]), -1)

    def test_ambiguous_training_row(self):
        raw = np.array([[1,2,3],[1,2,3],[4,5,6]],dtype=np.float32)
        inds, approx, ambiguity = locate_training_rows(np.array([[1,2,3]],dtype=np.float32),raw)
        self.assertEqual(int(inds[0]), -1)
        self.assertTrue(bool(ambiguity[0]))

    def test_normalize(self):
        vec = l2_normalize(np.array([[3.0, 4.0], [0,5.0]], dtype=np.float32))
        np.testing.assert_allclose(np.linalg.norm(vec, axis=1), 1, atol=1e-6)

if __name__ == '__main__':
    unittest.main()
