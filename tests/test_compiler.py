import pytest

from conftest import tl

c = tl.compiler


def t(cmd):
    return ("terminal", {"command": cmd})


@pytest.mark.parametrize("text,rung,kind", [
    ("Never force-push; use --force-with-lease instead.", "guard", "regex"),
    ("never run `git push --force`", "guard", "regex"),
    ("Use pnpm instead of npm", "guard", "regex"),
    ("instead of pip install, use uv add", "guard", "regex"),
    ("don't edit .env", "guard", "path_glob"),
    ("never touch migrations/**", "guard", "path_glob"),
    ("Never modify the .github/workflows directory.", "guard", "path_glob"),
    ("Ask me before running `terraform apply`", "review", "regex"),
    ("stop using the web_search tool", "guard", "tool"),
    ("never push to main", "guard", "regex"),
    ("do not use rm -rf", "guard", "regex"),
    ("never skip pre-commit hooks", "guard", "regex"),
    ("never run /curl[^|]*\\|\\s*(ba)?sh/", "guard", "regex"),
])
def test_compile_text_reaches_enforced_rungs(text, rung, kind):
    out = c.compile_text(text)
    assert out["rung"] == rung, out
    assert out["match"]["kind"] == kind
    assert c.selftest(out) == []


@pytest.mark.parametrize("text", ["src/**/*.ts must not contain console.log", "never leave `print(` in src/*.py"])
def test_compile_text_checks(text):
    out = c.compile_text(text)
    assert out["rung"] == "check"
    assert out["check"]["must_not_match"]


@pytest.mark.parametrize("text", ["Always write tests first", "be more concise", "Explain your reasoning"])
def test_unparseable_text_stays_a_rule(text):
    assert c.compile_text(text)["rung"] == "rule"


def test_force_push_guard_spares_force_with_lease():
    m = c.compile_text("Never force-push; use --force-with-lease")["match"]
    assert c.evaluate_match(m, *t("git push --force origin main"))
    assert c.evaluate_match(m, *t("git push -f"))
    assert c.evaluate_match(m, *t("git push origin +main"))
    assert c.evaluate_match(m, *t("cd repo && git   push  --force"))
    assert c.evaluate_match(m, *t("git push --force-with-lease origin main")) is None
    assert c.evaluate_match(m, *t("git push origin main")) is None
    assert c.evaluate_match(m, *t("echo git pushed --force")) is None


def test_command_guard_word_boundaries():
    m = c.compile_text("use pnpm instead of npm")["match"]
    assert c.evaluate_match(m, *t("npm install"))
    assert c.evaluate_match(m, *t("pnpm install")) is None
    assert c.evaluate_match(m, *t("npx foo")) is None
    # also catches it hidden in execute_code
    assert c.evaluate_match(m, "execute_code", {"code": "subprocess.run('npm i', shell=True)"})


def test_path_guard_on_file_tools_and_shell_writes():
    m = c.compile_text("don't edit .env")["match"]
    assert c.evaluate_match(m, "write_file", {"path": ".env", "content": "x"})
    assert c.evaluate_match(m, "patch", {"path": "/repo/.env"})
    assert c.evaluate_match(m, *t("rm .env"))
    assert c.evaluate_match(m, *t("echo X=1 >> .env"))
    assert c.evaluate_match(m, "write_file", {"path": ".env.example"}) is None
    assert c.evaluate_match(m, *t("cat .env")) is None  # reading is fine
    assert c.evaluate_match(m, "read_file", {"path": ".env"}) is None


@pytest.mark.parametrize("glob,path,hit", [
    (".github/workflows", "/abs/repo/.github/workflows/x.yml", True),
    (".github/workflows", ".github/dependabot.yml", False),
    ("migrations/**", "app/migrations/001.sql", True),
    ("migrations/**", "migrationsx/1", False),
    ("src/**/*.ts", "src/b.ts", True),
    ("src/**/*.ts", "/r/src/a/b.ts", True),
    ("src/**/*.ts", "lib/b.ts", False),
    ("/etc/hosts", "/x/etc/hosts", False),
    ("*.lock", "a/yarn.lock", True),
])
def test_glob_semantics(glob, path, hit):
    assert bool(c._glob_hit(glob, path)) is hit


def test_tool_guard():
    m = c.normalize_match({"tool": "web_search", "kind": "tool"})
    assert c.evaluate_match(m, "web_search", {"query": "x"})
    assert c.evaluate_match(m, "web_extract", {"urls": ["x"]}) is None


def test_own_tools_are_never_guarded():
    m = {"tools": ["*"], "kind": "regex", "field": "any", "pattern": "force"}
    assert c.evaluate_match(m, "trust_ladder_learn", {"rule": "never force push"}) is None
    with pytest.raises(c.CompileError):
        c.normalize_match({"tool": "trust_ladder_task", "kind": "tool"})


@pytest.mark.parametrize("spec", [
    {"tool": "terminal", "kind": "regex", "pattern": ".*"},
    {"tool": "terminal", "kind": "regex", "pattern": "(a+)+$"},
    {"tool": "terminal", "kind": "regex", "pattern": "("},
    {"tool": "terminal", "kind": "regex", "pattern": "x" * 500},
    {"tool": "write_file", "kind": "path_glob", "pattern": "**/*"},
    {"tool": "*", "kind": "tool"},
    {"tool": "terminal", "kind": "nope", "pattern": "x"},
])
def test_unsafe_specs_rejected(spec):
    with pytest.raises(c.CompileError):
        c.normalize_match(spec)


def test_unless_exception():
    m = c.normalize_match({"tool": "terminal", "kind": "regex", "field": "command",
                           "pattern": r"git push .*--force", "unless": "--force-with-lease"})
    assert c.evaluate_match(m, *t("git push origin --force"))
    assert c.evaluate_match(m, *t("git push origin --force-with-lease")) is None


def test_verify_against_examples():
    m = c.normalize_match({"tool": "terminal", "kind": "regex", "field": "command", "pattern": r"\bnpm\b"})
    assert c.verify_against_examples("guard", m, {"tool": "terminal", "args": {"command": "npm i"}},
                                     {"tool": "terminal", "args": {"command": "pnpm i"}}) == []
    too_broad = c.normalize_match({"tool": "terminal", "kind": "regex", "field": "command", "pattern": "npm"})
    probs = c.verify_against_examples("guard", too_broad, {"tool": "terminal", "args": {"command": "npm i"}},
                                      {"tool": "terminal", "args": {"command": "pnpm i"}})
    assert any("good_example" in p for p in probs)
    assert c.verify_against_examples("guard", m, None, None)  # no proof -> problem


def test_checks():
    chk = c.normalize_check({"path_glob": "src/**/*.py", "must_not_match": r"breakpoint\(\)"})
    bad = {"path": "src/app.py", "content": "x = 1\nbreakpoint()\n"}
    assert "line 2" in c.evaluate_check(chk, "write_file", bad, "{}")
    assert c.evaluate_check(chk, "write_file", {"path": "src/app.py", "content": "x=1"}, "{}") is None
    assert c.evaluate_check(chk, "write_file", {"path": "tests/a.py", "content": "breakpoint()"}, "{}") is None
    assert c.evaluate_check(chk, "patch", {"path": "src/a.py", "new_string": "breakpoint()"}, "{}")
    hdr = c.normalize_check({"path_glob": "*.py", "must_match": "SPDX-License-Identifier"})
    assert c.evaluate_check(hdr, "write_file", {"path": "a.py", "content": "print(1)"}, "{}")
    out = c.normalize_check({"result_must_not_match": r"\d+ skipped", "tool": "terminal"})
    assert c.evaluate_check(out, "terminal", {"command": "pytest"}, "3 passed, 2 skipped")
