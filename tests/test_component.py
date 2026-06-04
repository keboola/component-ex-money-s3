import unittest

from component import max_incremental_value


class TestComponent(unittest.TestCase):
    def test_max_incremental_value(self):
        rows = [{"lastChangeDate": "2024-01-01"}, {"lastChangeDate": "2024-03-02"}, {"lastChangeDate": "2024-02-01"}]
        self.assertEqual(max_incremental_value(rows, "lastChangeDate"), "2024-03-02")

    def test_max_incremental_value_empty(self):
        self.assertIsNone(max_incremental_value([], "lastChangeDate"))

    def test_max_incremental_value_missing_field(self):
        self.assertIsNone(max_incremental_value([{"x": 1}], "lastChangeDate"))


if __name__ == "__main__":
    unittest.main()
