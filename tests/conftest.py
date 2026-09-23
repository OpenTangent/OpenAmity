import os
import tempfile
import pytest


@pytest.fixture(autouse=True, scope="session")
def isolate_xdg_data_for_tests():
    """Safety guard: isolates XDG_DATA_HOME for pytest sessions to ensure tests never
    accidentally create or modify files in the real application data directory (~/.var/app/com.openamity.OpenAmity/data).
    """
    temp_xdg = tempfile.mkdtemp(prefix="openamity_pytest_data_")
    old_xdg = os.environ.get("XDG_DATA_HOME")
    os.environ["XDG_DATA_HOME"] = temp_xdg

    yield temp_xdg

    if old_xdg is not None:
        os.environ["XDG_DATA_HOME"] = old_xdg
    else:
        os.environ.pop("XDG_DATA_HOME", None)

    import shutil
    shutil.rmtree(temp_xdg, ignore_errors=True)
