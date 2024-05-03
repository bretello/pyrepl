#   Copyright 2000-2004 Michael Hudson-Doyle <micahel@gmail.com>
#
#                        All Rights Reserved
#
#
# Permission to use, copy, modify, and distribute this software and
# its documentation for any purpose is hereby granted without fee,
# provided that the above copyright notice appear in all copies and
# that both that copyright notice and this permission notice appear in
# supporting documentation.
#
# THE AUTHOR MICHAEL HUDSON DISCLAIMS ALL WARRANTIES WITH REGARD TO
# THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY
# AND FITNESS, IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL,
# INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER
# RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF
# CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN
# CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.

import sys

import pytest

from pyrepl.historical_reader import HistoricalReader

from .infrastructure import EA, TestReader, read_spec

# this test case should contain as-verbatim-as-possible versions of
# (applicable) bug reports


class HistoricalTestReader(HistoricalReader, TestReader):
    pass


@pytest.mark.xfail(reason="event missing", run=False)
def test_transpose_at_start():
    read_spec([("transpose", [EA, ""]), ("accept", [""])])


def test_cmd_instantiation_crash():
    spec = [
        ("reverse-history-isearch", ["(r-search `') "]),
        (("key", "left"), [""]),
        ("accept", [""]),
    ]
    read_spec(spec, HistoricalTestReader)


@pytest.mark.skipif(
    sys.platform == "darwin" and sys.version_info < (3, 10, 9),
    reason="prepare() hangs due to termios.tcdrain hanging on MacOS https://github.com/python/cpython/issues/97001",
)
def test_signal_failure(monkeypatch):
    import os
    import pty
    import signal

    from pyrepl.unix_console import UnixConsole

    def failing_signal(a, b):
        raise ValueError

    def really_failing_signal(a, b):
        raise AssertionError

    mfd, sfd = pty.openpty()
    try:
        c = UnixConsole(sfd, sfd)
        c.prepare()
        c.restore()
        monkeypatch.setattr(signal, "signal", failing_signal)
        c.prepare()
        monkeypatch.setattr(signal, "signal", really_failing_signal)
        c.restore()
    finally:
        os.close(mfd)
        os.close(sfd)


def test_down_historicalreader():
    class HistoricalReaderWithHistory(HistoricalTestReader):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.history = ['print("hello")']
            self.historyi = 1

    read_spec(
        [
            ("up", ['print("hello")']),
            ("up", ['print("hello")', "! start of buffer "]),
            ("down", [""]),
            ("down", ["", "! end of buffer "]),
            ("accept", [""]),
        ],
        reader_class=HistoricalReaderWithHistory,
    )


def test_down_pythonicreader():
    from pyrepl.python_reader import PythonicReader

    class PythonicTestReader(PythonicReader, TestReader):
        def __init__(self, console):
            super().__init__(console, locals=[])

        pass

    read_spec(
        [
            ("up", ["", "! start of buffer "]),
            ("down", ["", "! end of buffer "]),
            ("accept", [""]),
        ],
        reader_class=PythonicTestReader,
    )


def test_down_pythonicreader_history():
    from pyrepl.python_reader import PythonicReader

    class PythonicTestReader(PythonicReader, TestReader):
        def __init__(self, console):
            super().__init__(console, locals=[])
            self.history = ['print("hello")']

    read_spec(
        [
            ("up", ['print("hello")']),
            ("up", ['print("hello")', "! start of buffer "]),
            ("down", [""]),
            ("down", ["", "! end of buffer "]),
            ("accept", [""]),
        ],
        reader_class=PythonicTestReader,
    )
