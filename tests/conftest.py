"""Load the plugin directory (its name has a hyphen) as the package ``trust_ladder``."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def _load_package():
    if "trust_ladder" in sys.modules:
        return sys.modules["trust_ladder"]
    spec = importlib.util.spec_from_file_location(
        "trust_ladder", PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)])
    module = importlib.util.module_from_spec(spec)
    sys.modules["trust_ladder"] = module
    spec.loader.exec_module(module)
    return module


tl = _load_package()


class FakeCtx:
    """Records registrations like Hermes's PluginContext and serves config + dispatch."""

    def __init__(self):
        self.tools, self.hooks, self.commands, self.cli = {}, {}, {}, {}
        self.config = {}
        self.cron = None
        self.dispatched = []
        self.terminal_result = '{"output": "ok", "exit_code": 0, "error": null}'
        self.posted = []

    def register_tool(self, name, toolset, schema, handler, **kw):
        self.tools[name] = {"schema": schema, "handler": handler, "toolset": toolset}

    def register_hook(self, name, cb, **kw):
        self.hooks.setdefault(name, []).append(cb)

    def register_command(self, name, handler, description="", args_hint="", **kw):
        self.commands[name] = handler

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def get_config(self, key, default=None):
        return self.config.get(key, default)

    def set_config(self, key, value):
        self.config[key] = value

    def current_cron_execution(self):
        return self.cron

    def post_message(self, text):
        self.posted.append(text)

    def dispatch_tool(self, name, args, **kw):
        self.dispatched.append((name, args, kw))
        return self.terminal_result


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes"))
    (tmp_path / "hermes").mkdir()
    return tmp_path / "hermes"


@pytest.fixture()
def ctx(home):
    c = FakeCtx()
    tl.register(c)
    return c


@pytest.fixture()
def ladder(ctx):
    # the TrustLadder instance is bound into the registered handlers
    return ctx.tools["trust_ladder_learn"]["handler"].__self__
