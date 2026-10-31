import os, sys, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ids import normalize_id


class TestIds(unittest.TestCase):
    def test_pads_short_ids(self):
        self.assertEqual(normalize_id("12345"), "00012345")

    def test_keeps_exact(self):
        self.assertEqual(normalize_id("12345678"), "12345678")

    def test_long_ids_keep_last_8(self):
        self.assertEqual(normalize_id("9912345678"), "12345678")

    def test_strips_whitespace(self):
        self.assertEqual(normalize_id(" 77 "), "00000077")


if __name__ == "__main__":
    unittest.main()
