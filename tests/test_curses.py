import sys

import pytest

import pyrepl
from pyrepl.curses import setupterm


def test_setupterm(monkeypatch):
    assert setupterm(None, 0) is None

    exit_code = -1 if sys.platform == "darwin" else 0
    with pytest.raises(
        pyrepl._minimal_curses.error,
        match=rf"setupterm\(b?'term_does_not_exist', 0\) failed \(err={exit_code}\)",
    ):
        setupterm("term_does_not_exist", 0)

    monkeypatch.setenv("TERM", "xterm")
    assert setupterm(None, 0) is None

    monkeypatch.delenv("TERM")
    with pytest.raises(
        pyrepl._minimal_curses.error,
        match=r"setupterm\(None, 0\) failed \(err=-1\)",
    ):
        setupterm(None, 0)
