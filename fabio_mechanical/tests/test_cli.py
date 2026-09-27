from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

import pytest

from fabio_mechanical.cli.main import build_parser, main

ROOT = Path(__file__).resolve().parents[1]


def documented_commands(doc: str) -> list[str]:
    text = (ROOT / doc).read_text(encoding="utf-8")
    cmds: list[str] = []
    for block in re.findall(r"```(?:bash|sh|powershell)?\n(.*?)```", text, flags=re.S):
        joined = re.sub(r"[\\`]\n\s*", " ", block)
        cmds += [ln.strip() for ln in joined.splitlines() if ln.strip().startswith("fabio ")]
    return cmds


def test_help_and_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as e:
        main(["--help"])
    assert e.value.code == 0 and "backtest" in capsys.readouterr().out
    with pytest.raises(SystemExit) as e:
        main(["--version"])
    assert e.value.code == 0


@pytest.mark.parametrize("doc", ["FIRST_REAL_TEST.md", "README.md", "DATA_GUIDE.md", "RESEARCH_PROTOCOL.md"])
def test_documented_commands_parse(doc: str) -> None:
    cmds = documented_commands(doc)
    assert cmds, f"no fabio commands found in {doc}"
    parser = build_parser()
    for cmd in cmds:
        parser.parse_args(shlex.split(cmd, posix=True)[1:])


def test_schema_and_config_commands(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["schema", "hash"]) == 0
    assert "CONTRACT_SCHEMA_SHA256=" in capsys.readouterr().out
    assert main(["config", "validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["valid"] is True and out["ai_enabled"] is False


def test_data_commands_fail_cleanly_without_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    rc = main(["data", "cost", "--symbol", "NQ.v.0", "--schema", "mbo", "--start", "2026-01-06T00:00", "--end", "2026-01-07T00:00"])
    err = capsys.readouterr().err
    assert rc == 2 and ("DATABENTO_API_KEY" in err or "databento" in err.lower())
    rc = main(["data", "download", "--schema", "mbo", "--start", "2026-01-06", "--end", "2026-01-07", "--output", "x.dbn.zst"])
    assert rc == 2 and "spending cap" in capsys.readouterr().err


def test_synthetic_replay_features_backtest_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    ev = tmp_path / "mr.jsonl"
    assert main(["data", "synthetic", "--scenario", "mean_reversion_short", "--output", str(ev)]) == 0
    assert main(["replay", "run", "--input", str(ev)]) == 0
    capsys.readouterr()
    feats = tmp_path / "snaps.jsonl"
    assert main(["features", "build", "--input", str(ev), "--output", str(feats), "--sample-ms", "60000", "--compact"]) == 0
    lines = feats.read_text(encoding="utf-8").splitlines()
    assert 10 < len(lines) < 200 and json.loads(lines[0])["provenance"]["export_view"] == "compact"
    run = tmp_path / "run"
    assert main(["backtest", "run", "--input", str(ev), "--output-dir", str(run)]) == 0
    for name in (
        "manifest.json",
        "summary.json",
        "trades.csv",
        "fills.csv",
        "state_transitions.csv",
        "candidates.csv",
        "journal.jsonl",
        "REPORT.md",
    ):
        assert (run / name).exists(), name
    manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    assert {"config_hash", "schema_sha256", "code", "dataset", "invariant_hash"} <= set(manifest)
    assert manifest["dataset"]["dataset_sha256"] and manifest["code"]["source_sha256"]
    assert "MEAN_REVERSION" in (run / "REPORT.md").read_text(encoding="utf-8")
    assert main(["report", "--run-dir", str(run)]) == 0
    assert main(["research", "monte-carlo", "--trades", str(run / "trades.csv"), "--iterations", "50"]) == 0


def test_research_cli_requires_frozen_baseline(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "opt.json"
    rc = main(
        ["research", "optimize", "--synthetic", "mean_reversion_short", "--baseline", str(tmp_path / "missing.json"), "--output", str(out)]
    )
    assert rc == 2 and "frozen" in capsys.readouterr().err
    base = tmp_path / "baseline.json"
    assert main(["research", "freeze-baseline", "--synthetic", "mean_reversion_short", "--output", str(base)]) == 0
    rc = main(
        [
            "research",
            "optimize",
            "--synthetic",
            "mean_reversion_short",
            "--baseline",
            str(base),
            "--output",
            str(out),
            "--dims",
            "stop_buffer_ticks",
            "--mode",
            "grid",
            "--max-candidates",
            "2",
        ]
    )
    assert rc == 0 and json.loads(out.read_text(encoding="utf-8"))["candidates_evaluated"] == 2
