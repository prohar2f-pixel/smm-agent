import logging
import unittest


class SafeLoggingTests(unittest.TestCase):
    def test_noisy_http_loggers_are_silenced(self):
        from safe_logging import configure_safe_logging

        configure_safe_logging()

        self.assertGreaterEqual(logging.getLogger("httpx").level, logging.WARNING)
        self.assertGreaterEqual(logging.getLogger("httpcore").level, logging.WARNING)


if __name__ == "__main__":
    unittest.main()
