import contextlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Callable

from rich.console import Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
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
    module-level helper functions (:func:`add_overall`, :func:`advance_overall`,
    :func:`get_progress_callback`, etc.) rather than accessing ``_p`` directly.
    """

    def __init__(self):
        self.started = False
        self.progress = Progress(
            TextColumn("[cyan]{task.description}"),
            BarColumn(bar_width=None),
            # Pourcentage entier : la décimale n'apporte rien sur une barre de
            # quelques centaines de pixels, et sa largeur variable (« 9.8% » vs
            # « 100.0% ») faisait danser les colonnes qui suivent. `>3` cale
            # « 0% », « 42% » et « 100% » sur la même largeur.
            "[progress.percentage]{task.percentage:>3.0f}%",
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
        self._overall_total = 0

        # Spinner shown while a batch of track metadata is being resolved,
        # before any per-track download bar exists. Without it the overall bar
        # sits frozen at 0/N during the seconds spent on the first batch of
        # gw.get_track calls, which reads as a hang.
        self._resolve_progress = Progress(
            SpinnerColumn(),
            TextColumn("[yellow]{task.description}"),
            console=console,
        )
        self._resolve_task: int | None = None

        self.task_titles: list[str] = []
        self.prefix = Text.assemble(("Downloading ", "bold cyan"), overflow="ellipsis")
        self._text_cache = self.gen_title_text()
        self.live = Live(self._build_group(), refresh_per_second=10)

    def _build_group(self) -> Group:
        parts: list = [self.get_title_text()]
        if self._overall_task is not None:
            parts.append(self._overall_progress)
        if self._resolve_task is not None:
            parts.append(self._resolve_progress)
        parts.append(self.progress)
        return Group(*parts)

    def add_overall(self, total: int, description: str = "Overall") -> None:
        """Add *total* tracks to the overall track-count progress bar.

        Called once per album or playlist as its download starts. When several
        items download concurrently — multiple URLs on one command line, or the
        albums of an artist — their track counts accumulate into a single bar
        rather than each item replacing the previous one's total (which left the
        bar showing one album's count while every item advanced it, so it
        overflowed past ``N/N``). The bar is created on first use.

        Args:
            total: Number of tracks in the item that is starting; added to the
                running total. Non-positive values are ignored.
            description: Label shown to the left of the bar, used only when the
                bar is first created.
        """
        if total <= 0:
            return
        if not self.started:
            self.live.start()
            self.started = True
        self._overall_total += total
        if self._overall_task is None:
            self._overall_task = self._overall_progress.add_task(
                description, total=self._overall_total
            )
        else:
            self._overall_progress.update(self._overall_task, total=self._overall_total)
        self.live.update(self._build_group())

    def advance_overall(self) -> None:
        """Advance the overall bar by one track (success, failure, or dry-run)."""
        if self._overall_task is not None:
            self._overall_progress.advance(self._overall_task)
            self.live.update(self._build_group())

    def set_resolving(self, description: str) -> None:
        """Show (or relabel) the metadata-resolution spinner.

        Starts the ``Live`` display on the first call so the spinner is visible
        even if no overall bar or per-track bar exists yet.

        Args:
            description: Label shown next to the spinner, e.g.
                ``"Resolving 50 tracks"``.
        """
        if not self.started:
            self.live.start()
            self.started = True
        if self._resolve_task is None:
            self._resolve_task = self._resolve_progress.add_task(
                description, total=None
            )
        else:
            self._resolve_progress.update(self._resolve_task, description=description)
        self.live.update(self._build_group())

    def clear_resolving(self) -> None:
        """Hide the metadata-resolution spinner.

        A no-op when the spinner is not currently shown.
        """
        if self._resolve_task is not None:
            self._resolve_progress.remove_task(self._resolve_task)
            self._resolve_task = None
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
        task and running total so that the next :meth:`add_overall` call starts
        fresh.
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
            self._overall_total = 0
            if self._resolve_task is not None:
                self._resolve_progress.remove_task(self._resolve_task)
                self._resolve_task = None

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


def add_overall(total: int, description: str = "Overall ") -> None:
    """Add *total* tracks to the global overall-progress bar.

    Accumulates across concurrently-downloading items (several URLs, or an
    artist's albums) into one bar; creates the bar on first use.

    Args:
        total: Number of tracks in the item that is starting; added to the
            running total. Non-positive values are ignored.
        description: Label shown to the left of the bar, used only when the bar
            is first created.
    """
    global _p
    _p.add_overall(total, description)


def advance_overall() -> None:
    """Advance the global overall-progress bar by one track."""
    global _p
    _p.advance_overall()


@contextlib.contextmanager
def resolving(description: str, enabled: bool = True) -> Iterator[None]:
    """Show the metadata-resolution spinner for the duration of the block.

    Use around an ``await`` that resolves a batch of track metadata before any
    download bar exists, so the display does not look frozen. The spinner is
    always hidden again on exit, including on exception.

    Args:
        description: Label shown next to the spinner, e.g.
            ``"Resolving 50 tracks"``.
        enabled: When ``False``, the manager is left untouched (so callers need
            not branch on the ``progress_bars`` config).

    Yields:
        ``None``; the spinner is active only within the ``with`` block.
    """
    global _p
    if not enabled:
        yield
        return
    _p.set_resolving(description)
    try:
        yield
    finally:
        _p.clear_resolving()


def clear_progress() -> None:
    """Stop the global ``Live`` display and reset all progress state."""
    global _p
    _p.cleanup()
