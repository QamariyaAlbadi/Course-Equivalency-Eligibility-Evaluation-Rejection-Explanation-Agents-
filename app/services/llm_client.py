"""Calls Gemini with a model fallback. If the preferred model is overloaded (503/UNAVAILABLE),
rate limited (429) or not found (404), the next configured model is tried. Other errors
(bad key, invalid request) are raised, never hidden."""

import time

from app.config import GEMINI_MODELS


def _retryable(exc: Exception) -> bool:
    msg = str(exc)
    code = getattr(exc, "code", None)
    return code in (404, 429, 500, 503) or any(x in msg for x in ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED", "404", "NOT_FOUND"))


def generate_with_fallback(client, contents, config, models=None, pause: float = 2.0):
    """Returns the Gemini response of the first model that works. Raises the last error if all fail."""
    errors = []
    models = list(models or GEMINI_MODELS)
    for i, model in enumerate(models):
        try:
            return client.models.generate_content(model=model, contents=contents, config=config)
        except Exception as exc:  # noqa: BLE001
            if not _retryable(exc):
                raise
            errors.append(f"{model}: {type(exc).__name__}: {str(exc)[:160]}")
            if i < len(models) - 1:
                time.sleep(pause)
    raise RuntimeError("All configured Gemini models failed. " + " | ".join(errors))