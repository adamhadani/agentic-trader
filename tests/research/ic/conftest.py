import pytest

from tests.research.ic.world import World, make_world


@pytest.fixture
def ic_world() -> World:
    """A fresh synthetic cube build (8 names, 3 sectors plus one unknown), SPY, groups and window."""
    return make_world()
