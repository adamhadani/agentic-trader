"""Filing veto config: defaults, the YAML `off` spelling, bounds, windows and the loader passing `filing_veto:`."""

import textwrap

import pytest
import yaml
from pydantic import ValidationError

from agentic_trader.config import AppConfig, FilingVetoConfig, load_config
from agentic_trader.data.edgar_filings import FilingCategory


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def test_defaults():
    cfg = FilingVetoConfig()
    assert cfg.capture is False and cfg.entry_block == "off"
    assert (cfg.late_filing_days, cfg.adverse_8k_days, cfg.offering_days) == (30, 60, 7)
    assert cfg.pre_scan_minutes == 30 and cfg.max_age_seconds == 345600
    assert AppConfig().filing_veto == cfg


def test_windows_are_keyed_by_category():
    windows = FilingVetoConfig(late_filing_days=10, adverse_8k_days=20, offering_days=3).windows()
    assert windows == {FilingCategory.LATE_FILING: 10, FilingCategory.ADVERSE_8K: 20, FilingCategory.OFFERING: 3}
    assert all(isinstance(key, FilingCategory) for key in windows)


def test_a_bare_yaml_off_means_off_and_on_stays_invalid():
    assert FilingVetoConfig(**yaml.safe_load("entry_block: off")).entry_block == "off"
    for mode in ("preview", "enforce"):
        assert FilingVetoConfig(entry_block=mode).entry_block == mode
    with pytest.raises(ValidationError):
        FilingVetoConfig(**yaml.safe_load("entry_block: on"))
    with pytest.raises(ValidationError):
        FilingVetoConfig(entry_block="block")


@pytest.mark.parametrize("field", ["late_filing_days", "adverse_8k_days", "offering_days"])
def test_windows_run_from_one_to_one_hundred_twenty_days(field):
    assert getattr(FilingVetoConfig(**{field: 1}), field) == 1
    assert getattr(FilingVetoConfig(**{field: 120}), field) == 120
    for bad in (0, 121):
        with pytest.raises(ValidationError):
            FilingVetoConfig(**{field: bad})


@pytest.mark.parametrize(
    "bad",
    [{"pre_scan_minutes": 9}, {"pre_scan_minutes": 121}, {"max_age_seconds": 0}, {"unknown": 1}],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValidationError):
        FilingVetoConfig(**bad)


def test_pre_scan_bounds_are_inclusive():
    assert FilingVetoConfig(pre_scan_minutes=10).pre_scan_minutes == 10
    assert FilingVetoConfig(pre_scan_minutes=120).pre_scan_minutes == 120


def test_loader_passes_the_block(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            filing_veto:
              capture: true
              entry_block: off
              adverse_8k_days: 45
              pre_scan_minutes: 20
            database: {path: test.db}
            """
        )
    )
    config = load_config(str(path), environ=PRODUCTION)
    assert config.filing_veto.capture is True and config.filing_veto.entry_block == "off"
    assert config.filing_veto.adverse_8k_days == 45 and config.filing_veto.pre_scan_minutes == 20


def test_enforce_categories_default_to_all_three():
    cfg = FilingVetoConfig()
    assert cfg.enforce_categories == (
        FilingCategory.LATE_FILING,
        FilingCategory.ADVERSE_8K,
        FilingCategory.OFFERING,
    )
    assert cfg.enforced_windows() == cfg.windows()


def test_enforce_categories_load_from_yaml_strings():
    cfg = FilingVetoConfig(**yaml.safe_load('enforce_categories: ["late_filing", "adverse_8k"]'))
    assert cfg.enforce_categories == (FilingCategory.LATE_FILING, FilingCategory.ADVERSE_8K)
    assert cfg.enforced_windows() == {
        FilingCategory.LATE_FILING: cfg.late_filing_days,
        FilingCategory.ADVERSE_8K: cfg.adverse_8k_days,
    }


@pytest.mark.parametrize("bad", [[], ["offering", "offering"], ["late_filing", "bogus"]])
def test_enforce_categories_reject_empty_duplicate_and_unknown(bad):
    with pytest.raises(ValidationError):
        FilingVetoConfig(enforce_categories=bad)
