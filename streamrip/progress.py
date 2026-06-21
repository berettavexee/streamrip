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

    def get_callback(self, total: int, desc: str):
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
        self.task_titles.append(title.strip())
        self._text_cache = self.gen_title_text()

    def remove_title(self, title: str):
        try:
            self.task_titles.remove(title.strip())
        except ValueError:
            pass
        self._text_cache = self.gen_title_text()

    def gen_title_text(self) -> Rule:
        titles = ", ".join(self.task_titles[:3])
        if len(self.task_titles) > 3:
            titles += "..."
        t = self.prefix + Text(titles)
        return Rule(t)

    def get_title_text(self) -> Rule:
        return self._text_cache


@dataclass(slots=True)
class Handle:
    update: Callable[[int], None]
    done: Callable[[], None]

    def __enter__(self):
        return self.update

    def __exit__(self, *_):
        self.done()


# global instance
_p = ProgressManager()


def get_progress_callback(enabled: bool, total: int, desc: str) -> Handle:
    global _p
    if not enabled:
        return Handle(lambda _: None, lambda: None)
    return _p.get_callback(total, desc)


def add_title(title: str):
    global _p
    _p.add_title(title)


def remove_title(title: str):
    global _p
    _p.remove_title(title)


def set_overall(total: int, description: str = "Overall") -> None:
    global _p
    _p.set_overall(total, description)


def advance_overall() -> None:
    global _p
    _p.advance_overall()


def clear_progress():
    global _p
    _p.cleanup()
