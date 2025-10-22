"""CLI helper utilities for argument processing."""

from __future__ import annotations


class ArgumentParserError(Exception):
    """Raised when arguments are incorrectly specified."""


class ArgumentProcessor:
    """Helper to reorder global options before subcommands."""

    def __init__(self, subcommands: set[str]) -> None:
        self.global_options = {
            "-d",
            "--dir",
            "-f",
            "--file",
            "-q",
            "--quiet",
            "--version",
            "--cleanup",
            "--vacuum",
        }
        self.options_with_values = {"-d", "--dir", "-f", "--file"}
        self.subcommands = subcommands
        self.global_args: list[str] = []
        self.command_args: list[str] = []
        self.found_command = False
        self.expecting_value_for: str | None = None

    def process(self, argv: list[str]) -> list[str]:
        for arg in argv:
            self._process_argument(arg)

        if self.expecting_value_for:
            raise ArgumentParserError(
                f"option {self.expecting_value_for} requires an argument"
            )

        return self.global_args + self.command_args

    def _process_argument(self, arg: str) -> None:
        if self.expecting_value_for:
            self._handle_expected_value(arg)
        elif self._is_option_with_equals(arg):
            self._handle_option_with_equals(arg)
        elif arg in self.global_options:
            self._handle_global_option(arg)
        elif arg in self.subcommands and not self.found_command:
            self._handle_subcommand(arg)
        else:
            self._handle_command_arg(arg)

    def _handle_expected_value(self, value: str) -> None:
        option = self.expecting_value_for
        if option is None:
            raise ArgumentParserError("unexpected state: no option awaiting value")
        self.global_args.extend([option, value])
        self.expecting_value_for = None

    def _handle_option_with_equals(self, arg: str) -> None:
        option, value = arg.split("=", 1)
        if option in self.global_options:
            if option in self.options_with_values:
                if not value:
                    raise ArgumentParserError(f"option {option} requires an argument")
                self.global_args.extend([option, value])
            else:
                self.global_args.append(option)
                if value:
                    self.command_args.insert(0, value)
        else:
            self._handle_command_arg(arg)

    def _handle_global_option(self, option: str) -> None:
        if option in self.options_with_values:
            self.expecting_value_for = option
        else:
            self.global_args.append(option)

    def _handle_subcommand(self, command: str) -> None:
        self.found_command = True
        self.command_args.append(command)

    def _handle_command_arg(self, arg: str) -> None:
        if not self.found_command:
            self.found_command = True
        self.command_args.append(arg)

    @staticmethod
    def _is_option_with_equals(arg: str) -> bool:
        return arg.startswith("-") and "=" in arg


def rearrange_args(argv: list[str]) -> list[str]:
    if not argv:
        return argv

    subcommands = {
        "run",
        "init",
        "validate-taskspec",
        "queue",
        "queue read",
        "queue write",
        "queue peek",
        "queue move",
        "queue list",
        "queue watch",
    }

    # For nested commands we only track the top-level keyword
    top_level = {cmd.split()[0] for cmd in subcommands}
    processor = ArgumentProcessor(top_level)
    return processor.process(argv)


__all__ = ["rearrange_args", "ArgumentParserError"]
