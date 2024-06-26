#   Copyright 2000-2010 Michael Hudson-Doyle <micahel@gmail.com>
#                       Antonio Cuni
#                       Armin Rigo
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

import contextlib
import errno
import os
import re
import select
import signal
import struct
import termios
import time
from fcntl import ioctl
from typing import List, Optional, Tuple, Union

from . import curses
from .console import Console, Event
from .fancy_termios import tcgetattr, tcsetattr
from .trace import trace
from .unix_eventqueue import EventQueue


class InvalidTerminal(RuntimeError):
    pass


_error = (termios.error, curses.error, InvalidTerminal)

# there are arguments for changing this to "refresh"
SIGWINCH_EVENT = "repaint"

FIONREAD = getattr(termios, "FIONREAD", None)
TIOCGWINSZ = getattr(termios, "TIOCGWINSZ", None)


def _my_getstr(capability: str, optional: bool = False):
    r = curses.tigetstr(capability)
    if not optional and r is None:
        raise InvalidTerminal(
            f"terminal doesn't have the required '{capability}' capability"
        )
    return r


rates = {}
for rate in (
    0,
    50,
    75,
    110,
    134,
    150,
    200,
    300,
    600,
    1200,
    1800,
    2400,
    4800,
    9600,
    19200,
    38400,
    57600,
    115200,
    230400,
    460800,
):
    name = f"B{rate}"
    with contextlib.suppress(AttributeError):
        rates[getattr(termios, name)] = rate

del rate

delayprog = re.compile(b"\\$<([0-9]+)((?:/|\\*){0,2})>")

try:
    poll = select.poll
except AttributeError:
    # this is exactly the minumum necessary to support what we
    # do with poll objects
    class poll:
        def __init__(self):
            pass

        def register(self, fd, flag):
            self.fd = fd

        def poll(self, timeout=None):
            r, w, e = select.select([self.fd], [], [], timeout)
            return r


POLLIN = getattr(select, "POLLIN", None)


required_curses_tistrings = ("bel", "clear", "cup", "el")
optional_curses_tistrings = (
    "civis",
    "cnorm",
    "cub",
    "cub1",
    "cud",
    "cud1",
    "cuf",
    "cuf1",
    "cuu",
    "cuu1",
    "dch",
    "dch1",
    "hpa",
    "ich",
    "ich1",
    "ind",
    "pad",
    "ri",
    "rmkx",
    "smkx",
)


class UnixConsole(Console):
    def __init__(
        self,
        f_in: int = 0,
        f_out: int = 1,
        term: Optional[str] = None,
        encoding: Optional[str] = None,
    ):
        super().__init__(encoding=encoding)
        self.__buffer: List[Tuple[Union[bytes, str], bool]] = []

        if isinstance(f_in, int):
            self.input_fd = f_in
        else:
            self.input_fd = f_in.fileno()

        if isinstance(f_out, int):
            self.output_fd = f_out
        else:
            self.output_fd = f_out.fileno()

        self.pollob = poll()
        self.pollob.register(self.input_fd, POLLIN)
        curses.setupterm(term, self.output_fd)
        self.term = term

        for name in required_curses_tistrings:
            setattr(self, f"_{name}", _my_getstr(name))

        for name in optional_curses_tistrings:
            setattr(self, f"_{name}", _my_getstr(name, optional=True))

        # work out how we're going to sling the cursor around
        # hpa don't work in windows telnet :-(
        if False and self._hpa:  # noqa: SIM223
            self.__move_x = self.__move_x_hpa
        elif self._cub and self._cuf:  # type: ignore[attr-defined]
            self.__move_x = self.__move_x_cub_cuf  # type: ignore[has-type]
        elif self._cub1 and self._cuf1:  # type: ignore[attr-defined]
            self.__move_x = self.__move_x_cub1_cuf1  # type: ignore[has-type]
        else:
            raise RuntimeError("insufficient terminal (horizontal)")

        if self._cuu and self._cud:  # type: ignore[attr-defined]
            self.__move_y = self.__move_y_cuu_cud
        elif self._cuu1 and self._cud1:  # type: ignore[attr-defined]
            self.__move_y = self.__move_y_cuu1_cud1
        else:
            raise RuntimeError("insufficient terminal (vertical)")

        if self._dch1:  # type: ignore[attr-defined]
            self.dch1 = self._dch1  # type: ignore[attr-defined]
        elif self._dch:  # type: ignore[attr-defined]
            self.dch1 = curses.tparm(self._dch, 1)  # type: ignore[attr-defined]
        else:
            self.dch1 = None

        if self._ich1:  # type: ignore[attr-defined]
            self.ich1 = self._ich1  # type: ignore[attr-defined]
        elif self._ich:  # type: ignore[attr-defined]
            self.ich1 = curses.tparm(self._ich, 1)  # type: ignore[attr-defined]
        else:
            self.ich1 = None

        self.__move = self.__move_short

        self.event_queue = EventQueue(self.input_fd, self.encoding)
        self.cursor_visible = 1

    def refresh(self, screen, c_xy):
        # this function is still too long (over 90 lines)
        cx, cy = c_xy
        if not self.__gone_tall:
            while len(self.screen) < min(len(screen), self.height):
                self.__hide_cursor()
                self.__move(0, len(self.screen) - 1)
                self.__write("\n")
                self.__posxy = 0, len(self.screen)
                self.screen.append("")
        else:
            while len(self.screen) < len(screen):
                self.screen.append("")

        if len(screen) > self.height:
            self.__gone_tall = 1
            self.__move = self.__move_tall

        px, py = self.__posxy
        old_offset = offset = self.__offset
        height = self.height

        # we make sure the cursor is on the screen, and that we're
        # using all of the screen if we can
        if cy < offset:
            offset = cy
        elif cy >= offset + height:
            offset = cy - height + 1
        elif offset > 0 and len(screen) < offset + height:
            offset = max(len(screen) - height, 0)
            screen.append("")

        oldscr = self.screen[old_offset : old_offset + height]
        newscr = screen[offset : offset + height]

        # use hardware scrolling if we have it.
        if old_offset > offset and self._ri:
            self.__hide_cursor()
            self.__write_code(self._cup, 0, 0)
            self.__posxy = 0, old_offset
            for _ in range(old_offset - offset):
                self.__write_code(self._ri)
                oldscr.pop(-1)
                oldscr.insert(0, "")
        elif old_offset < offset and self._ind:
            self.__hide_cursor()
            self.__write_code(self._cup, self.height - 1, 0)
            self.__posxy = 0, old_offset + self.height - 1
            for _ in range(offset - old_offset):
                self.__write_code(self._ind)
                oldscr.pop(0)
                oldscr.append("")

        self.__offset = offset

        for (
            y,
            oldline,
            newline,
        ) in zip(range(offset, offset + height), oldscr, newscr):
            if oldline != newline:
                self.__write_changed_line(y, oldline, newline, px)

        y = len(newscr)
        while y < len(oldscr):
            self.__hide_cursor()
            self.__move(0, y)
            self.__posxy = 0, y
            self.__write_code(self._el)
            y += 1

        self.__show_cursor()

        self.screen = screen
        self.move_cursor(cx, cy)
        self.flushoutput()

    def __write_changed_line(self, y, oldline, newline, px):
        # this is frustrating; there's no reason to test (say)
        # self.dch1 inside the loop -- but alternative ways of
        # structuring this function are equally painful (I'm trying to
        # avoid writing code generators these days...)
        x = 0
        minlen = min(len(oldline), len(newline))
        #
        # reuse the oldline as much as possible, but stop as soon as we
        # encounter an ESCAPE, because it might be the start of an escape
        # sequene
        # XXX unicode check!
        while x < minlen and oldline[x] == newline[x] and newline[x] != "\x1b":
            x += 1
        if oldline[x:] == newline[x + 1 :] and self.ich1:
            if (
                y == self.__posxy[1]
                and x > self.__posxy[0]
                and oldline[px:x] == newline[px + 1 : x + 1]
            ):
                x = px
            self.__move(x, y)
            self.__write_code(self.ich1)
            self.__write(newline[x])
            self.__posxy = x + 1, y
        elif x < minlen and oldline[x + 1 :] == newline[x + 1 :]:
            self.__move(x, y)
            self.__write(newline[x])
            self.__posxy = x + 1, y
        elif (
            self.dch1
            and self.ich1
            and len(newline) == self.width
            and x < len(newline) - 2
            and newline[x + 1 : -1] == oldline[x:-2]
        ):
            self.__hide_cursor()
            self.__move(self.width - 2, y)
            self.__posxy = self.width - 2, y
            self.__write_code(self.dch1)
            self.__move(x, y)
            self.__write_code(self.ich1)
            self.__write(newline[x])
            self.__posxy = x + 1, y
        else:
            self.__hide_cursor()
            self.__move(x, y)
            if len(oldline) > len(newline):
                self.__write_code(self._el)
            self.__write(newline[x:])
            self.__posxy = len(newline), y

        # XXX: check for unicode mess
        if "\x1b" in newline:
            # ANSI escape characters are present, so we can't assume
            # anything about the position of the cursor.  Moving the cursor
            # to the left margin should work to get to a known position.
            self.move_cursor(0, y)

    def __write(self, text: str):
        self.__buffer.append((text, False))

    def __write_code(self, fmt, *args):
        self.__buffer.append((curses.tparm(fmt, *args), True))

    def __maybe_write_code(self, fmt, *args):
        if fmt:
            self.__write_code(fmt, *args)

    def __move_y_cuu1_cud1(self, y: int):
        dy = y - self.__posxy[1]
        if dy > 0:
            self.__write_code(dy * self._cud1)  # type: ignore[attr-defined]
        elif dy < 0:
            self.__write_code((-dy) * self._cuu1)  # type: ignore[attr-defined]

    def __move_y_cuu_cud(self, y: int):
        dy = y - self.__posxy[1]
        if dy > 0:
            self.__write_code(self._cud, dy)  # type: ignore[attr-defined]
        elif dy < 0:
            self.__write_code(self._cuu, -dy)  # type: ignore[attr-defined]

    def __move_x_hpa(self, x: int):
        if x != self.__posxy[0]:
            self.__write_code(self._hpa, x)  # type: ignore[attr-defined]

    def __move_x_cub1_cuf1(self, x: int):
        dx = x - self.__posxy[0]
        if dx > 0:
            self.__write_code(self._cuf1 * dx)  # type: ignore[attr-defined]
        elif dx < 0:
            self.__write_code(self._cub1 * (-dx))  # type: ignore[attr-defined]

    def __move_x_cub_cuf(self, x: int):
        dx = x - self.__posxy[0]
        if dx > 0:
            self.__write_code(self._cuf, dx)  # type: ignore[attr-defined]
        elif dx < 0:
            self.__write_code(self._cub, -dx)  # type: ignore[attr-defined]

    def __move_short(self, x: int, y: int):
        self.__move_x(x)
        self.__move_y(y)

    def __move_tall(self, x: int, y: int):
        assert 0 <= y - self.__offset < self.height, y - self.__offset
        self.__write_code(self._cup, y - self.__offset, x)  # type: ignore[attr-defined]

    def move_cursor(self, x: int, y: int):
        if y < self.__offset or y >= self.__offset + self.height:
            self.event_queue.insert(Event("scroll", None))
        else:
            self.__move(x, y)
            self.__posxy = x, y
            self.flushoutput()

    def prepare(self):
        # per-readline preparations:
        self.__svtermstate = tcgetattr(self.input_fd)
        raw = self.__svtermstate.copy()
        raw.iflag |= termios.ICRNL
        raw.iflag &= ~(termios.BRKINT | termios.INPCK | termios.ISTRIP | termios.IXON)
        raw.cflag &= ~(termios.CSIZE | termios.PARENB)
        raw.cflag |= termios.CS8
        raw.lflag &= ~(
            termios.ICANON | termios.ECHO | termios.IEXTEN | (termios.ISIG * 1)
        )
        raw.cc[termios.VMIN] = 1
        raw.cc[termios.VTIME] = 0
        tcsetattr(self.input_fd, termios.TCSADRAIN, raw)

        self.screen = []
        self.height, self.width = self.getheightwidth()

        self.__buffer = []

        self.__posxy = 0, 0
        self.__gone_tall = 0
        self.__move = self.__move_short
        self.__offset = 0

        self.__maybe_write_code(self._smkx)

        with contextlib.suppress(ValueError):
            self.old_sigwinch = signal.signal(signal.SIGWINCH, self.__sigwinch)

    def restore(self):
        self.__maybe_write_code(self._rmkx)
        self.flushoutput()
        tcsetattr(self.input_fd, termios.TCSADRAIN, self.__svtermstate)

        if hasattr(self, "old_sigwinch"):
            try:
                signal.signal(signal.SIGWINCH, self.old_sigwinch)
                del self.old_sigwinch
            except ValueError:
                # signal only works in main thread.
                pass

    def __sigwinch(self, signum, frame):
        self.height, self.width = self.getheightwidth()
        self.event_queue.insert(Event("resize", None))
        if self.old_sigwinch != signal.SIG_DFL:
            self.old_sigwinch(signum, frame)

    def push_char(self, char: bytes):
        trace("push char {char!r}", char=char)
        self.event_queue.push(char)

    def get_event(self, block: bool = True):
        assert isinstance(block, bool)
        while self.event_queue.empty():
            while True:
                # All hail Unix!
                try:
                    self.push_char(os.read(self.input_fd, 1))
                except OSError as err:
                    if err.errno == errno.EINTR:
                        if not self.event_queue.empty():
                            return self.event_queue.get()
                        continue
                    raise
                else:
                    break
            if not block:
                break
        return self.event_queue.get()

    def wait(self):
        self.pollob.poll()

    def set_cursor_vis(self, vis):
        if vis:
            self.__show_cursor()
        else:
            self.__hide_cursor()

    def __hide_cursor(self):
        if self.cursor_visible:
            self.__maybe_write_code(self._civis)
            self.cursor_visible = 0

    def __show_cursor(self):
        if not self.cursor_visible:
            self.__maybe_write_code(self._cnorm)
            self.cursor_visible = 1

    def repaint_prep(self):
        if not self.__gone_tall:
            self.__posxy = 0, self.__posxy[1]
            self.__write("\r")
            ns = len(self.screen) * ["\000" * self.width]
            self.screen = ns
        else:
            self.__posxy = 0, self.__offset
            self.__move(0, self.__offset)
            ns = self.height * ["\000" * self.width]
            self.screen = ns

    if TIOCGWINSZ:

        def getheightwidth(self):
            try:
                return int(os.environ["LINES"]), int(os.environ["COLUMNS"])
            except KeyError:
                height, width = struct.unpack(
                    "hhhh", ioctl(self.input_fd, TIOCGWINSZ, "\000" * 8)
                )[0:2]
                if not height:
                    return 25, 80
                return height, width

    else:

        def getheightwidth(self):
            try:
                return int(os.environ["LINES"]), int(os.environ["COLUMNS"])
            except KeyError:
                return 25, 80

    def forgetinput(self):
        termios.tcflush(self.input_fd, termios.TCIFLUSH)

    def flushoutput(self):
        for text, iscode in self.__buffer:
            if iscode:
                self.__tputs(text)
            else:
                os.write(self.output_fd, text.encode(self.encoding, "replace"))
        del self.__buffer[:]

    def __tputs(self, fmt, prog=delayprog):
        """A Python implementation of the curses tputs function; the
        curses one can't really be wrapped in a sane manner.

        I have the strong suspicion that this is complexity that
        will never do anyone any good."""
        # using .get() means that things will blow up
        # only if the bps is actually needed (which I'm
        # betting is pretty unlkely)
        bps = rates.get(self.__svtermstate.ospeed)
        while True:
            m = prog.search(fmt)
            if not m:
                os.write(self.output_fd, fmt)
                break
            x, y = m.span()
            os.write(self.output_fd, fmt[:x])
            fmt = fmt[y:]
            delay = int(m.group(1))
            if "*" in m.group(2):
                delay *= self.height
            if self._pad:
                nchars = (bps * delay) / 1000
                os.write(self.output_fd, self._pad * nchars)
            else:
                time.sleep(delay / 1000.0)

    def finish(self):
        y = len(self.screen) - 1
        while y >= 0 and not self.screen[y]:
            y -= 1
        self.__move(0, min(y, self.height + self.__offset - 1))
        self.__write("\n\r")
        self.flushoutput()

    def beep(self):
        self.__maybe_write_code(self._bel)
        self.flushoutput()

    if FIONREAD:

        def getpending(self):
            e = Event("key", "", "")

            while not self.event_queue.empty():
                e2 = self.event_queue.get()
                e.data += e2.data
                e.raw += e.raw

            amount = struct.unpack("i", ioctl(self.input_fd, FIONREAD, "\0\0\0\0"))[0]
            data = os.read(self.input_fd, amount)
            raw = str(data, self.encoding, "replace")
            # XXX: something is wrong here
            e.data += raw
            e.raw += raw
            return e

    else:

        def getpending(self):
            e = Event("key", "", "")

            while not self.event_queue.empty():
                e2 = self.event_queue.get()
                e.data += e2.data
                e.raw += e.raw

            amount = 10000
            data = os.read(self.input_fd, amount)
            raw = str(data, self.encoding, "replace")
            # XXX: something is wrong here
            e.data += raw
            e.raw += raw
            return e

    def clear(self):
        self.__write_code(self._clear)
        self.__gone_tall = 1
        self.__move = self.__move_tall
        self.__posxy = 0, 0
        self.screen = []
