"""R0 proof that the independent backend test entrypoint works."""

from memento_backend import __version__


def test_backend_package_is_importable() -> None:
    assert __version__ == "0.1.0"
