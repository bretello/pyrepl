import pytest


@pytest.fixture(scope="session", autouse=True)
def isolate(tmp_path_factory):
    """ avoids writing history files and/or using user-specific settings """
    path = tmp_path_factory.mktemp("mock")
    home_dir = path / "home"
    home_dir.mkdir()


    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("HOME", str(home_dir))

    yield

    monkeypatch.undo()
