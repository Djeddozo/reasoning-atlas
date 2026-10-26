"""Thin alias for the old package name (RA-055): `hq` is now `engine`.

`import hq.db` returns the very module object `engine.db`, so state, patches and isinstance checks are shared.
Remove once nothing imports `hq` any more (the Pi's services and schedules move to the new name in RA-042).
"""
import importlib
import importlib.abc
import importlib.util
import sys

_NEW = "engine"


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    _specs = {}

    def find_spec(self, name, path=None, target=None):
        if name.startswith(__name__ + ".") and name != __name__ + ".__main__":
            return importlib.util.spec_from_loader(name, self)
        return None

    def create_module(self, spec):
        module = importlib.import_module(_NEW + spec.name[len(__name__):])
        self._specs[module.__name__] = module.__spec__
        return module

    def exec_module(self, module):
        # The import system stamps the alias spec on the module; give it back its own.
        module.__spec__ = self._specs.pop(module.__name__, module.__spec__)


if not any(isinstance(f, _Alias) for f in sys.meta_path):
    sys.meta_path.insert(0, _Alias())


def __getattr__(name):
    try:
        return importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as e:
        raise AttributeError(name) from e
