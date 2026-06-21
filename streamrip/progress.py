from dataclasses import dataclass
from typing import Callable

from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.rule import Rule
from rich.text import Text

from .console import console


class ProgressManager:
    """Manages the Rich ``Live`` display used during downloads.

    Renders a stacked group, from top to bottom:

    - A ``Rule`` title bar showing the album / playlist being downloaded.
    - An optional overall progress bar (``N/total • ETA``) in white, shown
      when an album or playlist download is active.
    - Per-track download bars in cyan (transfer speed + ETA).

    A single module-level instance ``_p`` is created at import time.  Use the
    module-level helper functions (:func:`set_overall`, :func:`advance_overall`,
    :func:`get_progress_callback`, etc.) rather than accessing ``_p`` directly.
    """

    def __init__(self):
        self.started = False
        self.progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=None),
            "[progress.percentage]{task.percentage:>3.1f}%",
            "•",
            TransferSpeedColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
        )
        self._overall_progress = Progress(
            TextColumn("[bold white]{task.description}"),
            BarColumn(
                bar_width=None,
                style="dim white",
                complete_style="white",
                finished_style="dim white",
            ),
            MofNCompleteColumn(),
            "•",
            TimeRemainingColumn(),
            console=console,
        )
        self._overall_task: int | None = None

        self.task_titles: list[str] = []
        self.prefix = Text.assemble(("Downloading ", "bold cyan"), overflow="ellipsis")
        self._text_cache = self.gen_title_text()
        self.live = Live(self._build_group(), refresh_per_second=10)

    def _build_group(self) -> Group:
        parts: list = [self.get_title_text()]
        if self._overall_task is not None:
            parts.append(self._overall_progress)
        parts.append(self.progress)
        return Group(*parts)

    def set_overall(self, total: int, description: str = "Overall") -> None:
        """Initialize (or reset) the overall track-count progress bar.

        Args:
            total: Total number of tracks in the current batch.
            description: Label shown to the left of the bar.
        """
        if not self.started:
            self.live.start()
            self.started = True
        if self._overall_task is not None:
            self._overall_progress.remove_task(self._overall_task)
        self._overall_task = self._overall_progress.add_task(description, total=total)
        self.live.update(self._build_group())

    def advance_overall(self) -> None:
        """Advance the overall bar by one track (success, failure, or dry-run)."""
        if self._overall_task is not None:
            self._overall_progress.advance(self._overall_task)
            self.live.update(self._build_group())

    def get_callback(self, total: int, desc: str) -> "Handle":
        """Create a per-track progress bar and return a :class:`Handle` for it.

        Starts the ``Live`` display on the first call.

        Args:
            total: Total bytes expected for the download.
            desc: Short label displayed next to the progress bar.

        Returns:
            A :class:`Handle` whose context manager yields an ``update(bytes)``
            callable and hides the bar on exit.
        """
        if not self.started:
            self.live.start()
            self.started = True

        task = self.progress.add_task(f"[cyan]{desc}", total=total)

        def _callback_update(x: int):
            self.progress.update(task, advance=x)
            self.live.update(self._build_group())

        def _callback_done():
            self.progress.update(task, visible=False)

        return Handle(_callback_update, _callback_done)

    def cleanup(self):
        """Stop the ``Live`` display and reset all progress state.

        Safe to call when the display was never started.  Clears the overall
        task so that the next :meth:`set_overall` call starts fresh.
        """
        if self.started:
            # Update to empty before stopping so live.stop() renders nothing,
            # leaving a clean terminal for any output printed after this call.
            self.live.update(Text(""))
            self.live.stop()
            self.started = False
            if self._overall_task is not None:
                self._overall_progress.remove_task(self._overall_task)
                self._overall_task = None

    def add_title(self, title: str):
        """Append *title* to the title bar (stripped of surrounding whitespace).

        Args:
            title: Album or playlist name to display.
        """
        self.task_titles.append(title.strip())
        self._text_cache = self.gen_title_text()

    def remove_title(self, title: str):
        """Remove the first occurrence of *title* from the title bar.

        A no-op when *title* is not currently displayed.

        Args:
            title: Previously added album or playlist name.
        """
        try:
            self.task_titles.remove(title.strip())
        except ValueError:
            pass
        self._text_cache = self.gen_title_text()

    def gen_title_text(self) -> Rule:
        """Render the current title list as a Rich ``Rule``.

        Shows at most three titles joined by commas; appends ``"..."`` when
        more are queued.

        Returns:
            A :class:`rich.rule.Rule` ready to be embedded in the live group.
        """
        titles = ", ".join(self.task_titles[:3])
        if len(self.task_titles) > 3:
            titles += "..."
        t = self.prefix + Text(titles)
        return Rule(t)

    def get_title_text(self) -> Rule:
        """Return the cached title ``Rule`` (rebuilt on each :meth:`add_title` / :meth:`remove_title`).

        Returns:
            The most recently generated :class:`rich.rule.Rule`.
        """
        return self._text_cache


@dataclass(slots=True)
class Handle:
    """Context manager wrapping a single per-track progress bar.

    Attributes:
        update: Callable that advances the bar by *n* bytes.
        done: Callable that hides the bar when the download finishes.
    """

    update: Callable[[int], None]
    done: Callable[[], None]

    def __enter__(self):
        return self.update

    def __exit__(self, *_):
        self.done()


# global instance
_p = ProgressManager()


def get_progress_callback(enabled: bool, total: int, desc: str) -> Handle:
    """Return a :class:`Handle` for a per-track progress bar, or a no-op handle.

    Args:
        enabled: When ``False``, returns a handle whose ``update`` and ``done``
            callables are no-ops so callers need not branch on config.
        total: Expected download size in bytes.
        desc: Label shown next to the bar.

    Returns:
        A :class:`Handle` context manager for the progress bar.
    """
    global _p
    if not enabled:
        return Handle(lambda _: None, lambda: None)
    return _p.get_callback(total, desc)


def add_title(title: str) -> None:
    """Append *title* to the global progress-bar title strip.

    Args:
        title: Album or playlist name to display.
    """
    global _p
    _p.add_title(title)


def remove_title(title: str) -> None:
    """Remove *title* from the global progress-bar title strip.

    Args:
        title: Previously added album or playlist name.
    """
    global _p
    _p.remove_title(title)


def set_overall(total: int, description: str = "Overall") -> None:
    """Initialize (or reset) the global overall-progress bar.

    Args:
        total: Total number of tracks in the current batch.
        description: Label shown to the left of the bar.
    """
    global _p
    _p.set_overall(total, description)


def advance_overall() -> None:
    """Advance the global overall-progress bar by one track."""
    global _p
    _p.advance_overall()


def clear_progress() -> None:
    """Stop the global ``Live`` display and reset all progress state."""
    global _p
    _p.cleanup()
