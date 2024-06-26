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

from typing import TYPE_CHECKING, Dict, List

from pyrepl import commands
from pyrepl.reader import Reader as R

if TYPE_CHECKING:
    from .console import Console
    from .reader import KeyMap


isearch_keymap: "KeyMap" = (
    *((f"\\{c:03o}", "isearch-end") for c in range(256) if chr(c) != "\\"),
    *(
        (c, "isearch-add-character")
        for c in map(chr, list(range(32, 127)))
        if c != "\\"
    ),
    *(
        (f"\\{c:03o}", "isearch-add-character")
        for c in range(256)
        if chr(c).isalpha() and chr(c) != "\\"
    ),
    ("\\\\", "self-insert"),
    (r"\C-r", "isearch-backwards"),
    (r"\C-s", "isearch-forwards"),
    (r"\C-c", "isearch-cancel"),
    (r"\C-g", "isearch-cancel"),
    (r"\<backspace>", "isearch-backspace"),
)

ISEARCH_DIRECTION_NONE = ""
ISEARCH_DIRECTION_BACKWARDS = "r"
ISEARCH_DIRECTION_FORWARDS = "f"


class next_history(commands.Command):
    def do(self):
        r = self.reader
        if r.historyi == len(r.history):
            r.error("end of history list")
            return
        r.select_item(r.historyi + 1)


class previous_history(commands.Command):
    def do(self):
        r = self.reader
        if r.historyi == 0:
            r.error("start of history list")
            return
        r.select_item(r.historyi - 1)


class restore_history(commands.Command):
    def do(self):
        r = self.reader
        if r.historyi != len(r.history) and r.get_unicode() != r.history[r.historyi]:
            r.buffer = list(r.history[r.historyi])
            r.pos = len(r.buffer)
            r.dirty = True


class first_history(commands.Command):
    def do(self):
        self.reader.select_item(0)


class last_history(commands.Command):
    def do(self):
        self.reader.select_item(len(self.reader.history))


class operate_and_get_next(commands.FinishCommand):
    def do(self):
        self.reader.next_history = self.reader.historyi + 1


class yank_arg(commands.Command):
    def do(self):
        r = self.reader
        if r.last_command is self.__class__:
            r.yank_arg_i += 1
        else:
            r.yank_arg_i = 0
        if r.historyi < r.yank_arg_i:
            r.error("beginning of history list")
            return
        a = r.get_arg(-1)
        # XXX how to split?
        words = r.get_item(r.historyi - r.yank_arg_i - 1).split()
        if a < -len(words) or a >= len(words):
            r.error("no such arg")
            return
        w = words[a]
        b = r.buffer
        o = len(r.yank_arg_yanked) if r.yank_arg_i > 0 else 0
        b[r.pos - o : r.pos] = list(w)
        r.yank_arg_yanked = w
        r.pos += len(w) - o
        r.dirty = True


class forward_history_isearch(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_FORWARDS
        r.isearch_start = r.historyi, r.pos
        r.isearch_term = ""
        r.dirty = True
        r.push_input_trans(r.isearch_trans)


class reverse_history_isearch(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_BACKWARDS
        r.dirty = True
        r.isearch_term = ""
        r.push_input_trans(r.isearch_trans)
        r.isearch_start = r.historyi, r.pos


class isearch_cancel(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_NONE
        r.pop_input_trans()
        r.select_item(r.isearch_start[0])
        r.pos = r.isearch_start[1]
        r.dirty = True


class isearch_add_character(commands.Command):
    def do(self):
        r = self.reader
        b = r.buffer
        r.isearch_term += self.event[-1]
        r.dirty = True
        p = r.pos + len(r.isearch_term) - 1
        if b[p : p + 1] != [r.isearch_term[-1]]:
            r.isearch_next()


class isearch_backspace(commands.Command):
    def do(self):
        r = self.reader
        if len(r.isearch_term) > 0:
            r.isearch_term = r.isearch_term[:-1]
            r.dirty = True
        else:
            r.error("nothing to rubout")


class isearch_forwards(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_FORWARDS
        r.isearch_next()


class isearch_backwards(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_BACKWARDS
        r.isearch_next()


class isearch_end(commands.Command):
    def do(self):
        r = self.reader
        r.isearch_direction = ISEARCH_DIRECTION_NONE
        r.console.forgetinput()
        r.pop_input_trans()
        r.dirty = True


class HistoricalReader(R):
    """Adds history support (with incremental history searching) to the
    Reader class.

    Adds the following instance variables:
      * history:
        a list of strings
      * historyi:
      * transient_history:
      * next_history:
      * isearch_direction, isearch_term, isearch_start:
      * yank_arg_i, yank_arg_yanked:
        used by the yank-arg command; not actually manipulated by any
        HistoricalReader instance methods.
    """

    def collect_keymap(self) -> "KeyMap":
        return super().collect_keymap() + (
            (r"\C-n", "next-history"),
            (r"\C-p", "previous-history"),
            (r"\C-o", "operate-and-get-next"),
            (r"\C-r", "reverse-history-isearch"),
            (r"\C-s", "forward-history-isearch"),
            (r"\M-r", "restore-history"),
            (r"\M-.", "yank-arg"),
            (r"\<page down>", "last-history"),
            (r"\<page up>", "first-history"),
        )

    def __init__(self, console: "Console"):
        super().__init__(console)
        self.history: List[str] = []
        self.historyi = 0
        self.transient_history: Dict[int, str] = {}
        self.next_history = None
        self.isearch_direction = ISEARCH_DIRECTION_NONE
        for c in (
            next_history,
            previous_history,
            restore_history,
            first_history,
            last_history,
            yank_arg,
            forward_history_isearch,
            reverse_history_isearch,
            isearch_end,
            isearch_add_character,
            isearch_cancel,
            isearch_add_character,
            isearch_backspace,
            isearch_forwards,
            isearch_backwards,
            operate_and_get_next,
        ):
            self.commands[c.__name__] = c
            self.commands[c.__name__.replace("_", "-")] = c
        from pyrepl import input

        self.isearch_trans = input.KeymapTranslator(
            isearch_keymap,
            invalid_cls=isearch_end,
            character_cls=isearch_add_character,
        )

    def select_item(self, i: int):
        self.transient_history[self.historyi] = self.get_str()
        buf = self.transient_history.get(i)
        self.buffer = list(self.history[i] if buf is None else buf)
        self.historyi = i
        self.pos = len(self.buffer)
        self.dirty = True

    def get_item(self, i):
        if i != len(self.history):
            return self.transient_history.get(i, self.history[i])

        return self.transient_history.get(i, self.get_str())

    def prepare(self):
        super().prepare()
        try:
            self.transient_history = {}
            if self.next_history is not None and self.next_history < len(self.history):
                self.historyi = self.next_history
                self.buffer[:] = list(self.history[self.next_history])
                self.pos = len(self.buffer)
                self.transient_history[len(self.history)] = ""
            else:
                self.historyi = len(self.history)
            self.next_history = None
        except:
            self.restore()
            raise

    def get_prompt(self, lineno, cursor_on_line):
        if cursor_on_line and self.isearch_direction != ISEARCH_DIRECTION_NONE:
            d = "rf"[self.isearch_direction == ISEARCH_DIRECTION_FORWARDS]
            return f"({d}-search `{self.isearch_term}') "

        return super().get_prompt(lineno, cursor_on_line)

    def isearch_next(self):
        st = self.isearch_term
        p = self.pos
        i = self.historyi
        s = self.get_str()
        forwards = self.isearch_direction == ISEARCH_DIRECTION_FORWARDS
        while True:
            p = s.find(st, p + 1) if forwards else s.rfind(st, 0, p + len(st) - 1)
            if p != -1:
                self.select_item(i)
                self.pos = p
                return
            if (forwards and i == len(self.history) - 1) or (not forwards and i == 0):
                self.error("not found")
                return

            if forwards:
                i += 1
                s = self.get_item(i)
                p = -1
            else:
                i -= 1
                s = self.get_item(i)
                p = len(s)

    def finish(self):
        super().finish()
        ret = self.get_str()
        for i, t in list(self.transient_history.items()):
            if i < len(self.history) and i != self.historyi:
                self.history[i] = t
        if ret:
            self.history.append(ret)


def test():
    from pyrepl.unix_console import UnixConsole

    reader = HistoricalReader(UnixConsole())
    reader.ps1 = "h**> "
    reader.ps2 = "h/*> "
    reader.ps3 = "h|*> "
    reader.ps4 = r"h\*> "
    while reader.readline():
        pass


if __name__ == "__main__":
    test()
