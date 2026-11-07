from treefort.context import Context
from treefort.runtime import Runtime


class BaseCommand:
    def __init__(self):
        pass


class Command(BaseCommand):
    _name: str
    _aliases: list[str] = []
    # Positional parameters that must receive the exact text the user typed, listed in
    # signature order. Fire literal-evaluates arguments, so `treefort switch 125` would reach
    # the command as an int and `switch 1.10` as a float; cli.replace_literal_args rewrites
    # these into quoted flags. A parameter that follows a non-literal one needs extra handling,
    # because fire fills positionals from the full signature.
    _literal_args: tuple[str, ...] = ()

    def __init__(self, runtime: Runtime):
        super().__init__()
        self._runtime = runtime

    @property
    def _context(self) -> Context:
        """Convenience access to the context"""
        return self._runtime.context

    @property
    def _logger(self):
        """Convenience access to the logger"""
        return self._runtime.logger

    @property
    def _ui_enabled(self) -> bool:
        """Whether transient UI such as spinners may render.

        Verbose mode already streams every execution trace, so a spinner would only duplicate it.
        """
        return not getattr(self._runtime, "verbose", False)
