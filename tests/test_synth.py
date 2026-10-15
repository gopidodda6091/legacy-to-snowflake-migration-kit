from __future__ import annotations

from pathlib import Path

from migrate import synth
from migrate.cli import discover

ROOT = Path(__file__).resolve().parents[1]


def test_generation_is_deterministic(erp_table) -> None:
    assert synth.generate_rows(erp_table, 20, seed=1) == synth.generate_rows(erp_table, 20, seed=1)


def test_composite_primary_keys_are_unique() -> None:
    tables, _ = discover(ROOT / "samples")
    inv = next(t for t in tables if t.name == "INVENTORY_SNAPSHOTS")
    rows = synth.generate_rows(inv, 300, seed=42)
    keys = {tuple(r[k] for k in inv.primary_key) for r in rows}
    assert len(keys) == 300


def test_committed_samples_match_generator(tmp_path: Path) -> None:
    tables, _ = discover(ROOT / "samples")
    for path in synth.generate_all(tables, tmp_path, seed=42):
        committed = ROOT / "samples" / "data" / path.name
        assert committed.read_bytes() == path.read_bytes(), path.name
