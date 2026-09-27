from __future__ import annotations

import re
import tracemalloc
import types
from pathlib import Path
from typing import Any

from fabio_mechanical.configs import load_config
from fabio_mechanical.ingest.synthetic import SyntheticMBO, utc_ns
from fabio_mechanical.replay import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
CODE = [p for p in ROOT.rglob("*.py") if "tests" not in p.relative_to(ROOT).parts]
TEXT_SUFFIXES = {".py", ".md", ".yaml", ".toml", ".txt", ".json", ".example", ".cfg", ".ini"}


def test_no_ai_runtime_and_no_live_order_paths() -> None:
    ai = re.compile(r"^\s*(import|from)\s+(openai|anthropic|torch|tensorflow|sklearn|transformers|langchain|xgboost|lightgbm)\b", re.M)
    live = re.compile(r"(MetaTrader5|order_send|send_order|place_order|submit_order\()")
    for p in CODE:
        src = p.read_text(encoding="utf-8")
        assert not ai.search(src), p
        assert not live.search(src), p
    assert load_config()["AI_ENABLED"] is False


def test_baseline_strategy_uses_no_excluded_indicators() -> None:
    banned = re.compile(r"\b(RSI|MACD|EMA|SMA|bollinger|stochastic)\b", re.I)
    for sub in ("strategy", "market", "orderflow", "profile", "decision", "risk", "execution", "backtest"):
        for p in (ROOT / sub).glob("*.py"):
            code = "\n".join(line.split("#", 1)[0] for line in p.read_text(encoding="utf-8").splitlines())
            assert not banned.search(code), p


def test_no_secrets_or_env_files_committed() -> None:
    assert not (ROOT / ".env").exists()
    key = re.compile(r"\bdb-[A-Za-z0-9]{20,}\b")
    assignment = re.compile(r"DATABENTO_API_KEY\s*=\s*['\"]?[A-Za-z0-9-]{8,}")
    for p in ROOT.rglob("*"):
        if p.is_file() and p.suffix in TEXT_SUFFIXES and "runs" not in p.parts:
            text = p.read_text(encoding="utf-8", errors="ignore")
            assert not key.search(text), p
            assert not assignment.search(text), p


def _bench_events(minutes: int) -> list[Any]:
    m = SyntheticMBO(seed=3)
    m.set_time(utc_ns(2026, 1, 13, 14, 30))
    m.seed_book(80160)
    m.balance(80160, 12, minutes * 60, step_s=0.5)
    return list(m.events())


def test_replay_memory_is_bounded_while_streaming() -> None:
    cfg = load_config()
    peaks = []
    for minutes in (10, 30):
        events = _bench_events(minutes)
        tracemalloc.start()
        eng = ReplayEngine(cfg)
        for e in events:
            eng.process(e)
        peaks.append(tracemalloc.get_traced_memory()[1])
        tracemalloc.stop()
    # 3x the data must not need ~3x the memory: state is bounded (rolling windows, per-price maps)
    assert peaks[1] < peaks[0] * 2.0, peaks


def test_sources_stream_lazily(tmp_path: Path) -> None:
    from fabio_mechanical.ingest.local import write_jsonl_events
    from fabio_mechanical.ingest.sources import JsonlSource

    p = write_jsonl_events(tmp_path / "e.jsonl", _bench_events(1)[:50])
    it = JsonlSource(p).iter_events()
    assert isinstance(it, types.GeneratorType)
    assert next(it).action.value in {"CLEAR", "ADD", "SNAPSHOT"}


def test_benchmark_command_runs(capsys: Any) -> None:
    from fabio_mechanical.cli.main import main

    assert main(["bench", "--minutes", "10"]) == 0
    out = capsys.readouterr().out
    assert "replay_events_per_second" in out
