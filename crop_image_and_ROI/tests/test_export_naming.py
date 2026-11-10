import unittest

from export_naming import layout_filename
from settings import CONFIG


class ExportNamingTests(unittest.TestCase):
    def test_names_follow_column_order_and_keep_unicode(self):
        self.assertEqual(layout_filename(["FBP", "SART", "本文方法"]),
                         "comparison__FBP__SART__本文方法.png")
        self.assertEqual(layout_filename(["Ours"], ".TIFF"), "comparison__Ours.tiff")

    def test_windows_invalid_characters_and_whitespace_are_replaced(self):
        self.assertEqual(layout_filename(['  A / B:*?<>|"\\\n方法.  ', ".."]),
                         "comparison__A_B_方法__method.png")

    def test_overlong_names_are_rejected_without_silently_losing_methods(self):
        with self.assertRaisesRegex(ValueError, "简称"):
            layout_filename(["a" * CONFIG["limits"]["filename_max_chars"]])


if __name__ == "__main__":
    unittest.main()
