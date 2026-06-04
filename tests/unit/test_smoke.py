"""
Smoke test — C1-T01.

Checks that the top-level package structure is importable once the skeleton
exists.  Fails with ImportError in the red phase (skeleton not yet created),
passes as soon as the directory layout and __init__.py files are in place.
"""


def test_core_package_importable():
    """
    The `core` package must be importable without errors.
    Validates that the repository skeleton (§2 conventions) is in place.
    """
    import core  # noqa: F401


def test_apps_package_importable():
    """
    The `apps` package must be importable without errors.
    Validates that the apps layer of the skeleton is in place.
    """
    import apps  # noqa: F401


def test_core_config_module_importable():
    """
    `core.config` must be importable.
    Validates that the config sub-package exists inside core (§6 conventions).
    """
    import core.config  # noqa: F401


def test_apps_api_core_module_importable():
    """
    `apps.api_core` must be importable.
    Validates that the api-core application module exists inside apps (§2).
    """
    import apps.api_core  # noqa: F401
