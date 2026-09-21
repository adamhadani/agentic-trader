from datetime import UTC, datetime

from agentic_trader.agent.regime import RegimeSnapshot, VolatilityRegime
from agentic_trader.backtest.models import BacktestResult
from agentic_trader.presentation.formatters import (
    ExecutionResultView,
    ManualCloseResultView,
    PerformanceSummaryReport,
    PortfolioStatusReport,
    PositionsReport,
    PositionView,
    TelegramHtmlFormatter,
    TerminalFormatter,
)
from agentic_trader.research.alpha.models import AlphaDefinition, RegistrySnapshot


def test_terminal_formatter_positions():
    # Empty positions
    empty_report = PositionsReport(positions=[], total_unrealized_pnl=0.0, active_count=0, total_pnl_str="+$0.00")
    empty_output = TerminalFormatter.format_positions_table(empty_report)
    assert "No active positions currently tracked" in empty_output

    # Active positions
    pos = PositionView(
        id=1,
        contract="/MES",
        direction="LONG",
        quantity=2.0,
        entry_price=5000.0,
        current_price=5010.0,
        stop_loss=4980.0,
        take_profit=5040.0,
        unrealized_pnl=100.0,
        pnl_str="+$100.00",
        qty_label="2x",
    )
    report = PositionsReport(
        positions=[pos],
        total_unrealized_pnl=100.0,
        active_count=1,
        total_pnl_str="+$100.00",
    )
    output = TerminalFormatter.format_positions_table(report)
    assert "#1 2x /MES LONG" in output
    assert "Entry: 5,000.00" in output
    assert "Total Unrealized PnL: +$100.00" in output


def test_terminal_formatter_status():
    report = PortfolioStatusReport(
        cash_base=100000.0,
        max_notional=60000.0,
        current_exposure=15000.0,
        effective_leverage=0.15,
        active_contract_count=1,
        telegram_configured=True,
        llm_model="gpt-5.6",
        macro_summary="No high-impact macro catalysts.",
        vix_regime="NORMAL",
        vix_value=16.5,
        tnx_value=4.25,
        dxy_value=103.5,
        breakout_allowed=True,
        recent_signals=[
            {
                "id": 42,
                "status": "EXECUTED",
                "timestamp": "2026-09-13",
                "contract": "/MES",
                "direction": "LONG",
                "strategy": "trend_pullback",
                "entry_price": "5000.00",
                "risk_dollars": 100.0,
            }
        ],
    )
    output = TerminalFormatter.format_status_dashboard(report)
    assert "Cash Base:            $100,000.00" in output
    assert "Active Exposure:      $15,000.00 (0.15x effective leverage)" in output
    assert "#42 [EXECUTED]" in output


def test_telegram_html_formatter():
    pos = PositionView(
        id=1,
        contract="SPY",
        direction="LONG",
        quantity=50.0,
        entry_price=500.0,
        current_price=505.0,
        stop_loss=495.0,
        take_profit=510.0,
        unrealized_pnl=250.0,
        pnl_str="+$250.00",
        qty_label="50 shs",
    )
    report = PositionsReport(
        positions=[pos],
        total_unrealized_pnl=250.0,
        active_count=1,
        total_pnl_str="+$250.00",
    )
    html = TelegramHtmlFormatter.format_positions_html(report)
    assert "<b>ACTIVE POSITIONS (1)</b>" in html
    assert "#1 50 shs SPY (LONG)" in html
    assert "Unrealized P&amp;L: <b>+$250.00</b>" in html

    # Execution success
    exec_view = ExecutionResultView(
        success=True,
        signal_id=10,
        contract="/MNQ",
        direction="LONG",
        fill_price=18000.0,
        order_id="ALPAC-1234",
        quantity=1.0,
        notional_value=36000.0,
        risk_dollars=200.0,
        stop_loss=17900.0,
        take_profit=18200.0,
    )
    exec_html = TelegramHtmlFormatter.format_execution_html(exec_view, "paper")
    assert "ORDER EXECUTED (PAPER)" in exec_html
    assert "ALPAC-1234" in exec_html

    # Manual close
    close_view = ManualCloseResultView(
        success=True,
        signal_id=10,
        contract="/MNQ",
        direction="LONG",
        exit_price=18100.0,
        realized_pnl=200.0,
    )
    close_html = TelegramHtmlFormatter.format_manual_close_html(close_view)
    assert "Position #10 Closed" in close_html
    assert "+$200.00" in close_html

    # Regime HTML
    regime = RegimeSnapshot(
        vix=17.5,
        vix_regime=VolatilityRegime.NORMAL,
        tnx=4.25,
        dxy=103.5,
        breakout_allowed=True,
        min_rr_threshold=2.0,
        timestamp=datetime.now(UTC),
        summary_text="Market conditions normal.",
    )
    regime_html = TelegramHtmlFormatter.format_macro_dashboard_html(regime)
    assert "NORMAL" in regime_html
    assert "Allowed" in regime_html

    # Performance HTML
    perf_report = PerformanceSummaryReport(
        total_pnl=1250.50,
        win_rate=66.7,
        wins=4,
        losses=2,
        profit_factor=2.45,
        gross_profit=2100.0,
        gross_loss=849.50,
        active_count=1,
        active_exposure=15000.0,
        recent_closed_trades=[
            {
                "id": 1,
                "contract": "/MES",
                "direction": "LONG",
                "strategy": "trend_pullback",
                "realized_pnl": 350.0,
                "exit_reason": "TAKE_PROFIT",
            }
        ],
    )
    perf_html = TelegramHtmlFormatter.format_performance_html(perf_report)
    assert "PERFORMANCE ATTRIBUTION" in perf_html
    assert "+$1,250.50" in perf_html
    assert "66.7%" in perf_html
    assert "2.45" in perf_html
    assert "#1" in perf_html

    # Backtest HTML
    bt_res = BacktestResult(
        starting_cash=100000.0,
        ending_equity=112500.0,
        strategy_pnl=10000.0,
        strategy_return_pct=10.0,
        cash_yield_pnl=2500.0,
        combined_total_pnl=12500.0,
        combined_return_pct=12.5,
        total_trades=20,
        winning_trades=13,
        losing_trades=7,
        win_rate=65.0,
        profit_factor=2.1,
        max_drawdown_pct=4.2,
        sharpe_ratio=1.85,
        sortino_ratio=2.4,
        annualized_return_pct=18.5,
        avg_trade_duration_bars=4.5,
    )
    bt_html = TelegramHtmlFormatter.format_backtest_html(bt_res, symbol="SPY", lookback="1y")
    assert "BACKTEST SIMULATION: SPY (1y)" in bt_html
    assert "+12.50%" in bt_html
    assert "65.0%" in bt_html
    assert "2.10" in bt_html


def test_presentation_error_and_edge_branches():
    """Test failure and loss branches across presentation formatters."""
    # Failed execution
    exec_fail = ExecutionResultView(
        success=False,
        signal_id=11,
        contract="SPY",
        direction="LONG",
        error_message="Insufficient buying power",
    )
    fail_html = TelegramHtmlFormatter.format_execution_html(exec_fail, "PAPER")
    assert "Execution Failed (PAPER)" in fail_html
    assert "Insufficient buying power" in fail_html

    # Failed manual close
    close_fail = ManualCloseResultView(
        success=False,
        signal_id=12,
        contract="SPY",
        direction="LONG",
        error_message="Position not found in broker",
    )
    close_fail_html = TelegramHtmlFormatter.format_manual_close_html(close_fail)
    assert "❌ Position not found in broker" in close_fail_html

    # Negative realized PnL close
    close_loss = ManualCloseResultView(
        success=True,
        signal_id=13,
        contract="/MES",
        direction="LONG",
        exit_price=5750.0,
        realized_pnl=-250.0,
    )
    close_loss_html = TelegramHtmlFormatter.format_manual_close_html(close_loss)
    assert "-$250.00" in close_loss_html

    # Performance report with no trades
    empty_perf = PerformanceSummaryReport(
        total_pnl=0.0,
        win_rate=0.0,
        wins=0,
        losses=0,
        profit_factor=0.0,
        gross_profit=0.0,
        gross_loss=0.0,
        recent_closed_trades=[],
    )
    empty_perf_html = TelegramHtmlFormatter.format_performance_html(empty_perf)
    assert "No closed trades recorded yet" in empty_perf_html


def test_format_alphas_dashboard_html_universe():
    targeted = AlphaDefinition("alpha_targeted", "Targeted", "close", eligible_symbols=("NVDA", "AMD"))
    global_definition = AlphaDefinition("alpha_global", "Global", "close")
    card = TelegramHtmlFormatter.format_alphas_dashboard_html(
        RegistrySnapshot(3, (), (targeted, global_definition)),
        evidence={"days": 7, "truncated": False, "candidates": []},
    )
    assert "2 research candidates" in card
    assert "Shadow candidates cannot place orders" in card
    assert "0 enabled for signals" in card
    assert "--auto-promote" not in card


def test_dashboard_counts_paper_probes():
    snapshot = RegistrySnapshot(3, (), (), (AlphaDefinition("alpha_p", "P", "close", timeframe="1d"),))
    text = TelegramHtmlFormatter.format_alphas_dashboard_html(
        snapshot, evidence={"days": 7, "candidates": [], "truncated": False}
    )
    assert "1 paper probe" in text


def _probe_row(**overrides):
    row = {
        "version_id": "v1",
        "alpha_id": "alpha_p",
        "symbols": ["AAPL"],
        "expires_at": "2026-10-01T00:00:00+00:00",
        "renewals": 0,
        "live": True,
        "blocked_reason": None,
        "forward": {"trades": 3, "unknown": 0, "cumulative_r": -1.5, "kill_r": -4.0, "killed": False},
        "days_remaining": 12.3,
        "kill_distance_r": 2.5,
    }
    row.update(overrides)
    return row


def test_dashboard_renders_a_line_per_live_probe_and_escapes_a_hostile_alpha_id():
    snapshot = RegistrySnapshot(3, (), (), (AlphaDefinition("alpha_p", "P", "close", timeframe="1d"),))
    probes = [_probe_row(alpha_id="<b>x</b>")]
    text = TelegramHtmlFormatter.format_alphas_dashboard_html(
        snapshot, evidence={"days": 7, "candidates": [], "truncated": False}, probes=probes
    )
    assert "&lt;b&gt;x&lt;/b&gt;" in text
    assert "<b>x</b>" not in text
    assert "12d left" in text
    assert "3 trades" in text
    assert "-1.50R" in text
    assert "2.50R to kill" in text


def test_dashboard_skips_a_non_live_probe_row():
    snapshot = RegistrySnapshot(3, (), (), (AlphaDefinition("alpha_p", "P", "close", timeframe="1d"),))
    probes = [_probe_row(live=False, blocked_reason="probe term expired; renew it or let it retire")]
    text = TelegramHtmlFormatter.format_alphas_dashboard_html(
        snapshot, evidence={"days": 7, "candidates": [], "truncated": False}, probes=probes
    )
    assert "d left" not in text


def test_dashboard_html_identical_with_and_without_the_probes_argument():
    snapshot = RegistrySnapshot(3, (), (), (AlphaDefinition("alpha_p", "P", "close", timeframe="1d"),))
    evidence = {"days": 7, "candidates": [], "truncated": False}
    omitted = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence)
    explicit_none = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence, probes=None)
    assert omitted == explicit_none


def test_dashboard_html_unchanged_for_an_empty_registry_even_with_probes_given():
    snapshot = RegistrySnapshot(3, (), ())
    evidence = {"days": 7, "candidates": [], "truncated": False}
    baseline = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence)
    with_probes = TelegramHtmlFormatter.format_alphas_dashboard_html(snapshot, evidence=evidence, probes=[_probe_row()])
    assert baseline == with_probes


def test_telegram_html_sanitizer():
    # 1. Unexpected closing tags (the exact error user hit)
    bad_tags = "<b>Hello</i></b></b>"
    clean = TelegramHtmlFormatter.sanitize_telegram_html(bad_tags)
    assert clean == "<b>Hello</b>"

    # 2. Unclosed tags
    unclosed = "<b>Unclosed bold and <i>italic"
    clean_unclosed = TelegramHtmlFormatter.sanitize_telegram_html(unclosed)
    assert clean_unclosed == "<b>Unclosed bold and <i>italic</i></b>"

    # 3. Raw math inequalities and ampersands
    raw_math = "Yield < 5% & Spread > 2:1, OAS <= 350 bps"
    clean_math = TelegramHtmlFormatter.sanitize_telegram_html(raw_math)
    assert "&lt;" in clean_math
    assert "&gt;" in clean_math
    assert "&amp;" in clean_math

    # 4. Markdown elements
    md = "**Bold** and *italic* and `code`\n### Section 1"
    clean_md = TelegramHtmlFormatter.sanitize_telegram_html(md)
    assert "<b>Bold</b>" in clean_md
    assert "<code>code</code>" in clean_md
    assert "<b>Section 1</b>" in clean_md

    # 5. Unsupported tags like <p>, <br>, <h1>
    unsupported = "<h1>Title</h1><p>Paragraph 1<br>Break</p>"
    clean_unsupported = TelegramHtmlFormatter.sanitize_telegram_html(unsupported)
    assert "<p>" not in clean_unsupported
    assert "<br>" not in clean_unsupported


def test_telegram_strip_html():
    sample = "<b>Bold</b> &amp; <code>code &lt;10Y&gt;</code><p>Paragraph</p>"
    stripped = TelegramHtmlFormatter.strip_html(sample)
    assert stripped == "Bold & code <10Y>\nParagraph"


def test_telegram_split_message():
    # Short message
    short = "Hello Telegram"
    assert TelegramHtmlFormatter.split_telegram_message(short, max_chunk_len=100) == ["Hello Telegram"]

    # Long message split across paragraph boundaries
    p1 = "<b>Paragraph 1</b>: " + ("A" * 200)
    p2 = "<b>Paragraph 2</b>: " + ("B" * 200)
    long_msg = f"{p1}\n\n{p2}"

    chunks = TelegramHtmlFormatter.split_telegram_message(long_msg, max_chunk_len=250)
    assert len(chunks) == 2
    assert "Paragraph 1" in chunks[0]
    assert "Paragraph 2" in chunks[1]
    # Verify each chunk has balanced tags
    for c in chunks:
        assert c.count("<b>") == c.count("</b>")


def test_accepted_order_does_not_invent_a_fill_price():
    view = ExecutionResultView(signal_id=1, contract="SPY", direction="LONG", fill_price=None)
    rendered = TelegramHtmlFormatter.format_execution_html(view)
    assert "ORDER ACCEPTED" in rendered
    assert "Awaiting broker fill" in rendered
    assert "ORDER EXECUTED" not in rendered
    assert "<b>Fill Price:</b> <code>0.00</code>" not in rendered
