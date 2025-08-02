"""Translation helper module.

This module wraps the translation functionality behind a simple API. It
currently uses the `googletrans` library when available. If network
translation fails for any reason (network issues, missing dependency,
rate limiting), the translator will gracefully return `None`. This
behaviour allows higher-level code to insert missing translations on
import without crashing the application.

If you wish to use a different translation provider, you can easily
subclass `BaseTranslator` or replace the `GoogleTranslator` entirely.
"""

from __future__ import annotations

from typing import Optional

try:
    # googletrans can occasionally be flaky. It depends on an active
    # internet connection and may require a newer version for best
    # results. We guard the import so that the application does not
    # crash if the library is absent.
    from googletrans import Translator as _GoogleTranslator
except Exception:
    _GoogleTranslator = None  # type: ignore


class BaseTranslator:
    """Abstract base class for translators."""

    def translate(self, text: str, src: str, dest: str) -> Optional[str]:
        raise NotImplementedError


class GoogleTranslator(BaseTranslator):
    """Translation implementation backed by googletrans.

    Attributes:
        _translator: The underlying googletrans client or None.
    """

    def __init__(self) -> None:
        if _GoogleTranslator is not None:
            try:
                # Use service_urls to explicitly point to default google domain.
                self._translator = _GoogleTranslator(service_urls=["translate.googleapis.com"])
            except Exception:
                self._translator = None
        else:
            self._translator = None

    def translate(self, text: str, src: str, dest: str) -> Optional[str]:
        """Translate text from src language to dest language.

        Args:
            text: The input string to translate.
            src: Source language code (e.g. 'en').
            dest: Target language code (e.g. 'es').

        Returns:
            The translated string, or None if translation fails.
        """
        if not text or not self._translator:
            return None
        try:
            result = self._translator.translate(text, src=src, dest=dest)
            return result.text
        except Exception:
            return None


def get_default_translator() -> BaseTranslator:
    """Return a default translator instance, falling back to a no-op."""
    return GoogleTranslator()