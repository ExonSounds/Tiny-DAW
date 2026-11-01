"""The update notice under the master row: checks GitHub once, in a background thread, when the
program starts (update_check.py), and says what it found. Clicking its link opens the page."""
import threading

from PySide6.QtCore import QObject, Signal

from .. import update_check
from ..version import REPOSITORY
from . import theme


class UpdateChecker(QObject):
    done = Signal(tuple)  # update_check.check()'s result, delivered on the GUI thread

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="update-check").start()

    def run(self):
        result = update_check.check()
        try:
            self.done.emit(result)
        except RuntimeError:
            pass  # the window closed before GitHub answered: nobody to tell


def notice_text(result):
    """Rich text for the notice: what the check found (None: still checking)."""
    if result is None:
        return "Checking for updates…"
    state, value, url = result
    style = f'style="color: {theme.DIMMED_TEXT.name()};"'  # (Qt's rich text has no "inherit")
    link = f'<a href="{url}" {style}>{REPOSITORY}</a>'
    if state == "newer":
        return f'Update available: {value} · <a href="{url}" {style}>get it on GitHub</a>'
    if state == "current":
        return f"Up to date · {link}"
    return f"Couldn't check for updates · {link}"
