import sys

import pytest

from pyrepl._minimal_curses import error, setupterm


@pytest.mark.xfail(sys.platform == "win32", reason="windows does not have _curses")
def test_imports():
    import _curses

    from pyrepl.curses import error, setupterm, tigetstr, tparm

    assert setupterm is _curses.setupterm
    assert tigetstr is _curses.tigetstr
    assert tparm is _curses.tparm
    assert error is _curses.error


def test_minimal_curses_setupterm(monkeypatch):
    assert setupterm(None, 0) is None

    exit_code = -1 if sys.platform == "darwin" else 0
    with pytest.raises(
        error,
        match=rf"setupterm\(b?'term_does_not_exist', 0\) failed \(err={exit_code}\)",
    ):
        setupterm("term_does_not_exist", 0)

    monkeypatch.setenv("TERM", "xterm")
    assert setupterm(None, 0) is None

    monkeypatch.delenv("TERM")
    with pytest.raises(
        error,
        match=r"setupterm\(None, 0\) failed \(err=-1\)",
    ):
        setupterm(None, 0)
