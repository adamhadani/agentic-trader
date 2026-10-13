"""News context config: defaults, validation, and the loader passing `news:` and `corporate_actions:`."""

import textwrap

import pytest

from agentic_trader.config import AppConfig, NewsConfig, load_config, news_live


PRODUCTION = {"COPILOT_ENV": "production", "COPILOT_ENV_FILE": ""}


def write(tmp_path, body):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(body))
    return str(path)


def test_defaults_match_the_spec():
    cfg = NewsConfig()
    assert cfg.enabled is True
    assert (cfg.window_hours, cfg.max_items_per_symbol, cfg.max_symbols_per_item) == (48, 12, 5)
    assert (cfg.max_scored_per_scan, cfg.max_headlines_recorded) == (6, 5)
    assert (cfg.chunk_size, cfg.max_items_per_fetch, cfg.max_requests_per_minute) == (50, 1000, 100)
    assert (cfg.fetch_timeout_seconds, cfg.score_timeout_seconds, cfg.model) == (15.0, 20.0, None)
    assert AppConfig().news == cfg


@pytest.mark.parametrize(
    "bad",
    [
        {"window_hours": 0},
        {"window_hours": 169},
        {"max_scored_per_scan": 0},
        {"fetch_timeout_seconds": 0},
        {"fetch_timeout_seconds": float("inf")},
        {"max_items_per_fetch": 10},
        {"unknown": 1},
    ],
)
def test_invalid_values_are_rejected(bad):
    with pytest.raises(ValueError):
        NewsConfig(**bad)


def test_loader_passes_news_and_corporate_actions_blocks(tmp_path):
    path = write(
        tmp_path,
        """
        news: {enabled: false, window_hours: 24, model: "openai/gpt-4o-mini"}
        corporate_actions: {entry_block: enforce, pre_days: 3}
        database: {path: test.db}
        """,
    )
    config = load_config(path, environ=PRODUCTION)
    assert config.news.enabled is False and config.news.window_hours == 24
    assert config.news.model == "openai/gpt-4o-mini"
    assert config.corporate_actions.entry_block == "enforce" and config.corporate_actions.pre_days == 3


def test_news_live_needs_the_flag_and_alpaca_data_keys():
    base = AppConfig()
    assert news_live(base) is False  # no keys
    keyed = base.model_copy(update={"alpaca_api_key": "k", "alpaca_api_secret": "s"})
    assert news_live(keyed) is True
    assert news_live(keyed.model_copy(update={"news": NewsConfig(enabled=False)})) is False
