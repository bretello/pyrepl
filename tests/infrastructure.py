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
from __future__ import annotations

from typing import List, Optional, Tuple, Union

from pyrepl.console import Console, Event
from pyrepl.reader import Reader


class EqualsAnything:
    def __eq__(self, other):
        return True


EA = EqualsAnything()


Command = Union[
    Tuple[str, Optional[str]],
    Union[Tuple[str], str],
]
ExpectedScreen = Optional[List[str]]
TestSpec = List[Tuple[Command, ExpectedScreen]]


class TestConsole(Console):
    def __init__(
        self,
        events: list[tuple[Command, ExpectedScreen]],
        verbose: bool = False,
    ):
        super().__init__(width=80, height=24, encoding="utf-8")
        self.events = events
        self.next_screen: list[str] | None = None
        self.verbose = verbose

    def refresh(self, screen, xy: tuple[int, int]):
        if self.next_screen is not None:
            assert (
                screen == self.next_screen
            ), f"[ {screen} != {self.next_screen} after {self.last_event_name} ]"

    def get_event(self, block: bool = True) -> Event:
        event: Command
        screen: ExpectedScreen
        event, screen = self.events.pop(0)
        self.next_screen = screen
        if not isinstance(event, tuple):
            event = (event, None)
        self.last_event_name = event[0]
        if self.verbose:
            print(f"{event=}")
        return Event(*event)

    def getpending(self) -> Event:
        """Nothing pending, but do not return None here."""
        return Event("key", "", "")


class TestReader(Reader):
    __test__ = False

    def get_prompt(self, lineno: int, cursor_on_line) -> str:
        return ""

    def refresh(self):
        Reader.refresh(self)
        self.dirty = True


def read_spec(test_spec: TestSpec, reader_class=TestReader):
    # remember to finish your test_spec with 'accept' or similar!
    con = TestConsole(test_spec, verbose=True)

    reader = reader_class(con)
    reader.readline()
