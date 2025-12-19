import unittest
from prefi import Prefi


class TestPrefi(unittest.TestCase):
    def setUp(self):
        self.prefi = Prefi('test_')

    def test_add_prefix(self):
        result = self.prefi.add_prefix('word')
        self.assertEqual(result, 'test_word')

    def test_remove_prefix(self):
        result = self.prefi.remove_prefix('test_word')
        self.assertEqual(result, 'word')

    def test_remove_prefix_no_match(self):
        result = self.prefi.remove_prefix('word')
        self.assertEqual(result, 'word')

    def test_empty_prefix(self):
        prefi = Prefi('')
        self.assertEqual(prefi.add_prefix('word'), 'word')


if __name__ == '__main__':
    unittest.main()
