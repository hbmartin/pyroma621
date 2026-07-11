"""Output formatting for pyroma.

Reporters are responsible for all user-facing *report* output. Diagnostic
messages (download progress, warnings from data collection) go through the
``pyroma`` logger instead, so that library users can control them with
standard logging configuration.

A reporter has three hooks, called in order by ``pyroma.run()``:

``start(argument)``
    Called before data collection begins, with the checked target.
``found(name)``
    Called when the package name has been determined.
``finish(rating)``
    Called with the final ``ratings.Rating``.
"""

import json
import sys
from typing import IO

from pyroma.ratings import MAX_RATING, Rating

_DASHES = "-" * 30


class TextReporter:
    """The classic human-readable pyroma output."""

    def __init__(self, stream: IO[str] | None = None, quiet: bool = False) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.quiet = quiet

    def _print(self, text: str) -> None:
        print(text, file=self.stream)

    def start(self, argument: str) -> None:
        if self.quiet:
            return
        self._print(_DASHES)
        self._print("Checking " + argument)

    def found(self, name: str) -> None:
        if self.quiet:
            return
        self._print("Found " + name)

    def finish(self, rating: Rating) -> None:
        if self.quiet:
            # Output only the rating.
            self._print(str(rating.rating))
            return
        self._print(_DASHES)
        for message in rating.messages:
            # XXX It would be nice with a * pointlist instead, but that requires
            # that we know how wide the terminal is and nice word-breaks, so that's
            # for later.
            self._print(message)
        if rating.problems:
            self._print(_DASHES)
        self._print(f"Final rating: {rating.rating}/{MAX_RATING}")
        self._print(rating.level)
        self._print(_DASHES)


class JsonReporter:
    """Machine-readable JSON output, printed as a single document."""

    def __init__(self, stream: IO[str] | None = None, quiet: bool = False) -> None:
        self.stream = stream if stream is not None else sys.stdout
        self.checked: str | None = None
        self.name: str | None = None

    def start(self, argument: str) -> None:
        self.checked = argument

    def found(self, name: str) -> None:
        self.name = name

    def finish(self, rating: Rating) -> None:
        document = {"checked": self.checked, "name": self.name}
        document.update(rating.as_dict())
        json.dump(document, self.stream, indent=2)
        self.stream.write("\n")


def get_reporter(output_format: str, quiet: bool = False) -> TextReporter | JsonReporter:
    if output_format == "json":
        return JsonReporter(quiet=quiet)
    return TextReporter(quiet=quiet)
