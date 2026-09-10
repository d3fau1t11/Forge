"""Execution backends — platform-specific process spawning."""
# Import lazily to avoid circular imports (local.py imports manager.py which
# imports execution_service which would re-enter this package before it is ready).
# Callers should import LocalBackend directly from backend.execution.backends.local.
