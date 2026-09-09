"""The CLI. Runs on `--fallback-only` so it never needs credentials."""

from __future__ import annotations

import json

import pytest

from app.cli import build_parser, main

ARGS = ["--fallback-only", "30x40 east facing site in Pune, 3BHK with pooja room"]


def test_json_on_stdout_status_on_stderr(capsys):
    """`naksha-intent ... | jq` has to work, so status must not pollute stdout."""
    assert main(ARGS) == 0
    out, err = capsys.readouterr()

    payload = json.loads(out)  # raises if anything non-JSON leaked onto stdout
    assert payload["brief"]["program"]["bedrooms"] == 3
    assert "[fallback]" in err


def test_summary_is_readable(capsys):
    assert main(["-s", *ARGS]) == 0
    out, _ = capsys.readouterr()

    assert "plot" in out and "9.14" in out
    assert "3BHK" in out
    assert "Pune" in out
    assert "assumed" in out


def test_summary_speaks_the_user_s_vocabulary(capsys):
    """'1 floor(s)' and 'east' are schema; 'ground floor' and 'east facing' are what
    the person typed, and reading it back is how they catch a misread."""
    assert main(["-s", *ARGS]) == 0
    out, _ = capsys.readouterr()

    assert "east facing" in out
    assert "ground floor" in out
    assert "floor(s)" not in out


def test_assumption_table_shows_a_confidence_per_row(capsys):
    """The confidence column is the point of the table — without it every guess
    looks equally solid, and stage \u2463 has nothing to threshold on."""
    assert main(["-s", *ARGS]) == 0
    out, _ = capsys.readouterr()

    rows = [ln for ln in out.splitlines() if ln.startswith("    bathrooms")]
    assert len(rows) == 1, out
    assert rows[0].split()[-1] == "0.8"


def test_long_reasons_wrap_under_their_own_column(capsys):
    """A wrapped reason that restarts at column 0 reads as a new assumption."""
    assert main(["-s", "--fallback-only", "I want a two bedroom house"]) == 0
    out, _ = capsys.readouterr()

    # Bounded to the assumption block: everything after it — the `ask` list — indents
    # to its own rules and would otherwise be counted as stray continuation lines.
    body = out.split("assumed\n", 1)[1].split("\n\n", 1)[0].splitlines()
    starts = {len(ln) - len(ln.lstrip()) for ln in body if ln.strip()}
    assert len(starts) == 2, body  # row indent, and one deeper continuation indent


def test_reads_stdin_when_piped(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakePipe("1200 sqft north facing Pune 2bhk"))
    assert main(["--fallback-only"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["brief"]["program"]["bedrooms"] == 2


def test_empty_stdin_is_an_error(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin", _FakePipe("   "))
    assert main(["--fallback-only"]) == 2
    assert "no input" in capsys.readouterr().err


def test_words_are_rejoined(capsys):
    """Unquoted input arrives as many argv entries; losing the spaces breaks parsing."""
    assert main(["--fallback-only", "30x40", "east", "facing", "3bhk"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["brief"]["plot"]["facing"] == "east"


def test_fallback_only_never_calls_a_provider(capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("--fallback-only must not reach a provider")

    monkeypatch.setattr("app.cli.extract_brief", boom)
    assert main(ARGS) == 0
    assert "--fallback-only" in capsys.readouterr().err


def test_provider_override_is_passed_through(capsys, monkeypatch):
    seen = {}

    def spy(text, *, settings=None, **kw):
        seen["provider"] = settings.intent_provider
        seen["model"] = settings.intent_model
        from app.ir.models import IntentResult, Provenance
        from app.llm import fallback

        return IntentResult(brief=fallback.parse(text), provenance=Provenance())

    monkeypatch.setattr("app.cli.extract_brief", spy)
    assert main(["-p", "openai", "-m", "gpt-5.2", "30x40 3bhk"]) == 0
    assert seen == {"provider": "openai", "model": "gpt-5.2"}


def test_unknown_provider_is_rejected_before_any_work():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["-p", "gemini", "x"])


class TestActionableErrors:
    """A fallback should say what to do, not just what broke."""

    def _run(self, reason: str, provider: str, capsys) -> str:
        from app.ir.models import IntentResult, Provenance
        from app.llm import fallback as fb

        def stub(text, *, settings=None, **kw):
            return IntentResult(
                brief=fb.parse(text),
                provenance=Provenance(
                    provider=provider, fallback_used=True, fallback_reason=reason
                ),
            )

        import app.cli as cli

        original = cli.extract_brief
        cli.extract_brief = stub
        try:
            main(["-s", "30x40 3bhk"])
        finally:
            cli.extract_brief = original
        return capsys.readouterr().err

    def test_missing_anthropic_key_offers_the_subscription_route(self, capsys):
        """The case that actually bit: an inferred anthropic route with no key,
        on a machine that already had a working Claude Code login."""
        err = self._run(
            'ProviderUnavailable: no usable credentials: "Could not resolve '
            'authentication method."',
            "anthropic",
            capsys,
        )
        assert "ANTHROPIC_API_KEY" in err
        assert "-p claude_code" in err

    def test_missing_openai_key_points_at_the_right_variable(self, capsys):
        err = self._run("ProviderUnavailable: AuthenticationError: 401", "openai", capsys)
        assert "OPENAI_API_KEY" in err
        assert "claude_code" not in err

    def test_unknown_model_says_how_to_route_it(self, capsys):
        err = self._run("provider: cannot tell which provider serves model 'x'", None, capsys)
        assert "NAKSHA_INTENT_PROVIDER" in err

    def test_ordinary_fallback_gets_no_noise(self, capsys):
        err = self._run("schema: model did not produce a valid Brief", "anthropic", capsys)
        assert "→" not in err


class TestRouteBanner:
    """The banner must name the provider, not just the model.

    Showing `model claude-opus-5` alone made an inferred API route look like it
    would use whatever credentials happened to be around.
    """

    def test_names_the_inferred_provider(self):
        from app.cli import _route
        from app.config import Settings

        assert "provider anthropic" in _route(Settings(intent_model="claude-opus-5"))
        assert "provider openai" in _route(Settings(intent_model="gpt-5.2"))

    def test_names_an_explicit_override(self):
        from app.cli import _route
        from app.config import Settings

        route = _route(
            Settings(intent_model="claude-opus-5", intent_provider="claude_code")
        )
        assert "provider claude_code" in route

    def test_unresolvable_model_does_not_raise(self):
        from app.cli import _route
        from app.config import Settings

        assert "no provider resolved" in _route(Settings(intent_model="llama-4-405b"))


class _FakePipe:
    def __init__(self, text: str) -> None:
        self._text = text

    def isatty(self) -> bool:
        return False

    def read(self) -> str:
        return self._text


class TestMultilineBriefs:
    """A pasted brief must not become two briefs.

    The failure was silent: both halves parsed, so a 4BHK joint-family brief came back
    as a 4BHK with no pooja room, and the "Vastu is very important" in the tail never
    reached the model at all.
    """

    def _reader(self, monkeypatch, lines, pending):
        from app import cli

        supplied = iter(lines)
        monkeypatch.setattr("builtins.input", lambda *a: next(supplied))
        # True while lines remain, so the reader sees a buffer the way a paste looks.
        monkeypatch.setattr(cli, "_input_pending", lambda: next(pending))
        return cli._read_brief

    def test_a_pasted_brief_is_read_as_one(self, monkeypatch):
        read = self._reader(
            monkeypatch,
            ["4BHK for a joint family,", "pooja room, Vastu is very important"],
            iter([True, False]),
        )
        assert read() == "4BHK for a joint family, pooja room, Vastu is very important"

    def test_a_typed_line_still_submits_on_enter(self, monkeypatch):
        read = self._reader(monkeypatch, ["30x40 3bhk"], iter([False]))
        assert read() == "30x40 3bhk"

    def test_trailing_backslash_continues_without_a_buffer(self, monkeypatch):
        """The explicit form, for typing a long brief by hand."""
        read = self._reader(
            monkeypatch, ["30x40 east facing \\", "3BHK with pooja"], iter([False])
        )
        assert read() == "30x40 east facing 3BHK with pooja"

    def test_captured_stdin_degrades_to_single_line_rather_than_crashing(self):
        from app import cli

        assert cli._input_pending() is False


class TestSvgOutput:
    """`--svg` is the only path from a solved bundle to something a person can judge.

    Bengaluru rather than `ARGS`' Pune: stage ② has no ruleset for Pune, so there is
    no envelope to solve inside and the flag would have nothing to draw.
    """

    # A plot that lays out legally. The 30x40 is 98% packed and its rooms come out
    # too small to carry their own labels, which would make this a test of that plot's
    # troubles rather than of the renderer.
    BRIEF = [
        "--fallback-only",
        "--allow-unverified",
        "30x50 3bhk in Bengaluru",
    ]

    def test_writes_a_parseable_drawing_naming_its_rooms(self, tmp_path, capsys):
        out = tmp_path / "plan.svg"
        assert main(["--svg", str(out), *self.BRIEF]) == 0

        from xml.etree import ElementTree

        drawing = out.read_text(encoding="utf-8")
        ElementTree.fromstring(drawing)  # raises on malformed SVG
        assert "master bedroom" in drawing
        assert "[svg]" in capsys.readouterr().err

    def test_the_artefact_replaces_the_json_dump_on_stdout(self, tmp_path, capsys):
        """Asking for a drawing is asking for the drawing, not the Brief as well.

        `-L` already behaves this way; the two flags have to agree or piping breaks
        depending on which artefact you asked for.
        """
        assert main(["--svg", str(tmp_path / "plan.svg"), *self.BRIEF]) == 0
        assert capsys.readouterr().out == ""

    def test_a_storey_is_a_sheet(self, tmp_path, capsys):
        """G+1 is two drawings. Merging them into one image would be a third thing."""
        out = tmp_path / "plan.svg"
        assert main(
            [
                "--svg",
                str(out),
                "--fallback-only",
                "--allow-unverified",
                "30x40 4bhk g+1 in Bengaluru with study and car parking",
            ]
        ) == 0
        assert not out.exists()
        assert (tmp_path / "plan-floor1.svg").exists()
        assert (tmp_path / "plan-floor2.svg").exists()

    def test_both_artefacts_come_from_one_solve(self, tmp_path, capsys):
        bundle, drawing = tmp_path / "plan.json", tmp_path / "plan.svg"
        assert main(["-L", str(bundle), "--svg", str(drawing), *self.BRIEF]) == 0

        rooms = {room["room_id"] for room in json.loads(bundle.read_text())["layouts"][0]["rooms"]}
        assert "hall" in rooms
        assert drawing.exists()

    def test_no_envelope_means_no_drawing_rather_than_an_empty_one(self, tmp_path, capsys):
        """A file that exists and shows nothing is worse than no file."""
        out = tmp_path / "plan.svg"
        assert main(["--svg", str(out), "--fallback-only", "30x40 3bhk in Pune"]) == 0
        assert not out.exists()
        assert "[no layout]" in capsys.readouterr().err
