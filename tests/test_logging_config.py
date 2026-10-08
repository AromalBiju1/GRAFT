"""Tests for application logging setup (logging_config.py)."""

from __future__ import annotations

import logging
import unittest

from logging_config import (
    NOISY_LOGGERS,
    configure_logging,
    get_logger,
    logging_config_dict,
    resolve_level,
)


class ResolveLevelTests(unittest.TestCase):
    def test_defaults_to_info(self) -> None:
        self.assertEqual(resolve_level(None), logging.INFO)

    def test_reads_a_level_name(self) -> None:
        self.assertEqual(resolve_level("debug"), logging.DEBUG)
        self.assertEqual(resolve_level("WARNING"), logging.WARNING)

    def test_unknown_name_falls_back_to_info(self) -> None:
        self.assertEqual(resolve_level("nonsense"), logging.INFO)

    def test_accepts_an_int(self) -> None:
        self.assertEqual(resolve_level(logging.ERROR), logging.ERROR)


class ConfigureLoggingTests(unittest.TestCase):
    """Each test starts from a known-clean logging state.

    configure_logging() is idempotent by design, so a handler left behind by an
    earlier test would suppress the setup under test. setUp clears state;
    tearDown restores it so the rest of the suite is unaffected.
    """

    def setUp(self) -> None:
        self._root = logging.getLogger()
        self._saved_handlers = list(self._root.handlers)
        self._saved_level = self._root.level
        self._saved_noisy = {n: logging.getLogger(n).level for n in NOISY_LOGGERS}
        self._root.handlers.clear()
        for name in NOISY_LOGGERS:
            logging.getLogger(name).setLevel(logging.NOTSET)

    def tearDown(self) -> None:
        root = logging.getLogger()
        root.handlers.clear()
        for handler in self._saved_handlers:
            root.addHandler(handler)
        root.setLevel(self._saved_level)
        for name, level in self._saved_noisy.items():
            logging.getLogger(name).setLevel(level)

    def test_repeated_calls_do_not_stack_handlers(self) -> None:
        """A second call must not duplicate output.

        Without the guard, every import-time call would add another handler and
        every log line would be emitted N times.
        """
        for _ in range(5):
            configure_logging("info")
        self.assertEqual(len(logging.getLogger().handlers), 1)

    def test_second_call_without_level_keeps_the_first_level(self) -> None:
        configure_logging("debug")
        configure_logging()  # no explicit level
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_explicit_level_on_a_later_call_is_honoured(self) -> None:
        """configure_logging is a no-op for handlers, not for the level."""
        configure_logging("info")
        configure_logging("debug")
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_force_replaces_handlers(self) -> None:
        configure_logging("info")
        configure_logging("info", force=True)
        self.assertEqual(len(logging.getLogger().handlers), 1)

    def test_sets_the_requested_level(self) -> None:
        configure_logging("debug")
        self.assertEqual(logging.getLogger().level, logging.DEBUG)

    def test_third_party_loggers_are_quieter(self) -> None:
        configure_logging("info")
        for name in NOISY_LOGGERS:
            self.assertEqual(
                logging.getLogger(name).level,
                logging.WARNING,
                f"{name} should be quieter than the root logger",
            )

    def test_records_reach_the_handler(self) -> None:
        import io

        stream = io.StringIO()
        configure_logging("info")
        handler = logging.getLogger().handlers[0]
        handler.setStream(stream)
        try:
            logging.getLogger("api.main").info("hello")
        finally:
            handler.setStream(__import__("sys").stderr)
        self.assertIn("hello", stream.getvalue())
        self.assertIn("api.main", stream.getvalue())

    def test_get_logger_configures_and_returns(self) -> None:
        logger = get_logger("graft.tests", "info")
        self.assertEqual(logger.name, "graft.tests")
        self.assertTrue(logging.getLogger().handlers)


class DictConfigTests(unittest.TestCase):
    def test_schema_is_valid_for_dictconfig(self) -> None:
        schema = logging_config_dict("info")
        self.assertEqual(schema["version"], 1)
        self.assertIn("console", schema["handlers"])
        self.assertEqual(schema["root"]["handlers"], ["console"])
        self.assertFalse(schema["disable_existing_loggers"])
        for name in NOISY_LOGGERS:
            self.assertEqual(schema["loggers"][name]["level"], "WARNING")

    def test_is_accepted_by_the_stdlib(self) -> None:
        import logging.config

        logging.config.dictConfig(logging_config_dict("info"))
        self.assertTrue(logging.getLogger().handlers)


if __name__ == "__main__":
    unittest.main()
