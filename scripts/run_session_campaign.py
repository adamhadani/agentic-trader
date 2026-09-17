"""Reproducible bounded session experiment using the existing replay service.

This research runner starts no daemon, submits no orders and changes no registry.
Its frozen protocol and every charged replay remain private diagnostic evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from agentic_trader.cli.commands.alpha import alpha_repository, research_environment
from agentic_trader.cli.utils import session_source
from agentic_trader.config import load_config
from agentic_trader.market.bars import SessionClockPolicy
from agentic_trader.market.session import ET_TZ
from agentic_trader.research.alpha.data import save_json_report
from agentic_trader.research.alpha.forecast_policy import BASIS_POINTS, MAX_SIDE_COST_BPS
from agentic_trader.research.alpha.models import AlphaDefinition, AlphaOrigin
from agentic_trader.research.alpha.replay import ReplayPlan, ReplayStatus
from agentic_trader.research.alpha.replay_workflow import AlphaReplayService
from agentic_trader.research.alpha.strategy import AlphaExecutionPolicy


MAX_CAMPAIGN_ATTEMPTS = 128


def campaign_jobs(protocol):
    """Expand and validate the complete frozen matrix before any I/O or computation."""
    symbols, costs, hypotheses = protocol["symbols"], protocol["cost_bps_per_side"], protocol["hypotheses"]
    windows = [(date.fromisoformat(a), date.fromisoformat(b)) for a, b in protocol["windows"]]
    if (
        not symbols
        or len(symbols) != len(set(symbols))
        or not hypotheses
        or len({h["id"] for h in hypotheses}) != len(hypotheses)
        or not costs
        or len(set(costs)) != len(costs)
        or any(not math.isfinite(c) or not 0 <= c <= MAX_SIDE_COST_BPS for c in costs)
        or not windows
        or windows != sorted(windows)
        or any(windows[i][1] >= windows[i + 1][0] for i in range(len(windows) - 1))
    ):
        raise ValueError("Unique hypotheses/symbols/costs and nonoverlapping chronological windows required")
    jobs = []
    for symbol in symbols:
        for window, (start, end) in enumerate(windows):
            for hypothesis in hypotheses:
                for cost in costs:
                    definition = AlphaDefinition(
                        f"{protocol['campaign_id']}_{hypothesis['id']}_{symbol}",
                        hypothesis["id"],
                        hypothesis["expression"],
                        description=hypothesis["rationale"],
                        origin=AlphaOrigin.MANUAL,
                        timeframe=protocol["timeframe"],
                        direction=protocol["direction"],
                        normalization_window=protocol["normalization_window"],
                        entry_threshold=protocol["entry_threshold"],
                        eligible_symbols=(symbol,),
                        data_feed=protocol["feed"],
                        semantics_version=3,
                        clock=SessionClockPolicy(**protocol["clock"]),
                        execution=AlphaExecutionPolicy(**protocol["execution"], friction_per_side=cost / BASIS_POINTS),
                    )
                    jobs.append(
                        {
                            "hypothesis": hypothesis["id"],
                            "symbol": symbol,
                            "window": window,
                            "cost_bps": cost,
                            "plan": ReplayPlan(symbol, start, end, definition),
                        }
                    )
    if not 1 <= len(jobs) == protocol["budget"] <= MAX_CAMPAIGN_ATTEMPTS:
        raise ValueError("Matrix must exactly match the bounded predeclared budget")
    if len({job["plan"].identity for job in jobs}) != len(jobs):
        raise ValueError("Duplicate experiment plans")
    if any(protocol["triage"][key] not in costs for key in ("primary_cost_bps", "stress_cost_bps")):
        raise ValueError("Triage costs must be in the frozen matrix")
    return jobs


class FrozenSessionSource:
    """One immutable read per cohort; comparisons cannot select different revisions.

    Failures are cached too: subsequent declared attempts retain the failure rather
    than obtain a more favorable data sample via an implicit provider retry.
    """

    def __init__(self, source):
        self.source = source
        self._cache = {}
        self.receipts = []

    def _once(self, method, args):
        key = (method, *args)
        if key not in self._cache:
            receipt = {
                "method": method,
                "arguments": [str(a) for a in args],
                "requested_at": datetime.now(UTC).isoformat(),
            }
            try:
                self._cache[key] = getattr(self.source, method)(*args)
            except Exception as exc:
                self._cache[key] = exc
                receipt["error_type"] = type(exc).__name__
            receipt["received_at"] = datetime.now(UTC).isoformat()
            self.receipts.append(receipt)
        value = self._cache[key]
        if isinstance(value, Exception):
            raise value
        return value.copy(deep=True) if isinstance(value, pd.DataFrame) else value

    def calendar(self, start, end):
        return self._once("calendar", (start, end))

    def minutes(self, symbol, start, end, feed):
        return self._once("minutes", (symbol, start, end, feed))


def summarize_trial(result, raw, cost):
    if result["status"] != ReplayStatus.COMPLETED:
        return {"status": result["status"], "error_type": result.get("error_type"), "error": result.get("error")}
    returns = pd.Series(
        [row["net_return"] for row in result["net_returns"]],
        index=pd.to_datetime([row["bar_start"] for row in result["net_returns"]], utc=True),
    )
    if returns.empty or not np.isfinite(returns).all() or (returns <= -1).any():
        raise ValueError("Complete finite research equity clock required")
    prices = raw.rename(columns=str.lower)
    # One fully funded passive entry, marked (not liquidated), like terminal model inventory.
    benchmark = float(
        prices.loc[returns.index[-1], "close"] / prices.loc[returns.index[0], "open"] - 1 - cost / BASIS_POINTS
    )
    logs = np.log1p(returns)
    daily = logs.groupby(returns.index.tz_convert(ET_TZ).date).sum()
    features = result["signal_feature_coverage"]
    coverage = result["coverage"]
    return {
        "status": result["status"],
        "net_return": float(np.expm1(logs.sum())),
        "benchmark_return": benchmark,
        "daily_log_returns": daily.tolist(),
        "execution_minutes": len(returns),
        "coverage_complete": coverage["missing_minutes"] == 0
        and coverage["observed_minutes"] == coverage["expected_minutes"] == len(returns),
        "feature_coverage": features["scored_bars"] / features["bars"] if features["bars"] else 0,
        "closed_trades": result["total_trades"],
        "entries": len(result["entries"]),
        "long_entries": sum(e["direction"] == 1 for e in result["entries"]),
        "short_entries": sum(e["direction"] == -1 for e in result["entries"]),
        "open_position": result["open_position"],
        "pending_entry": result["pending_entry"],
    }


def triage(primary, stressed, policy, *, expected_blocks):
    result = {"advance_to_further_research": False, "authorizes_promotion": False, "reasons": []}
    rows = [*primary, *stressed]
    if (
        len(primary) != expected_blocks
        or len(stressed) != expected_blocks
        or any(r["status"] != ReplayStatus.COMPLETED for r in rows)
    ):
        result["reasons"] = ["incomplete_experiment"]
        return result

    def wealth(rows, field):
        return float(np.prod([1 + r[field] for r in rows]) - 1)

    net, benchmark, stress = (
        wealth(primary, "net_return"),
        wealth(primary, "benchmark_return"),
        wealth(stressed, "net_return"),
    )
    daily = [v for r in primary for v in r["daily_log_returns"]]
    net_log = sum(daily)
    influence = max(daily) / net_log if net_log > 0 else None
    checks = {
        "coverage": all(
            r["coverage_complete"] and r["feature_coverage"] >= policy["minimum_feature_coverage"] for r in rows
        ),
        "closed_trades": sum(r["closed_trades"] for r in primary) >= policy["minimum_closed_trades"],
        "positive_blocks": sum(r["net_return"] > 0 for r in primary) >= policy["minimum_positive_blocks"],
        "primary_net_and_excess": not policy["require_positive_primary_net_and_excess"]
        or (net > 0 and net > benchmark),
        "stress_net": not policy["require_positive_stress_net"] or stress > 0,
        "concentration": influence is not None and influence <= policy["maximum_positive_day_share_of_net_log_gain"],
    }
    result.update(
        advance_to_further_research=all(checks.values()),
        reasons=[k for k, v in checks.items() if not v],
        primary_net_return=net,
        benchmark_return=benchmark,
        stress_net_return=stress,
        largest_positive_day_share_of_net_log_gain=influence,
        closed_trades=sum(r["closed_trades"] for r in primary),
    )
    return result


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def execute_campaign(protocol_path: Path, output: Path, repository, adapter, *, environment):
    protocol = json.loads(await asyncio.to_thread(protocol_path.read_text))
    jobs = await asyncio.to_thread(campaign_jobs, protocol)
    as_of = pd.Timestamp.now(tz="UTC")
    if any(job["plan"].end_at > as_of for job in jobs):
        raise ValueError("Campaign requires fully elapsed historical windows")
    await asyncio.to_thread(output.mkdir, parents=True, mode=0o700)  # Refuse overwrite/reuse of a previous campaign.
    protocol_hash = await asyncio.to_thread(file_hash, protocol_path)
    manifest = {
        "protocol": protocol,
        "protocol_hash": protocol_hash,
        "environment": environment,
        "runner_hash": await asyncio.to_thread(file_hash, Path(__file__)),
        "plans": [j["plan"].document() for j in jobs],
        "started_at": datetime.now(UTC).isoformat(),
    }
    await asyncio.to_thread(save_json_report, manifest, output / "manifest.json")
    rows = []
    source = FrozenSessionSource(adapter)
    service = AlphaReplayService(repository, source)
    for number, job in enumerate(jobs):
        plan = job["plan"]
        folder = output / f"trial-{number:03d}"
        result = await service.run(
            plan, folder, environment={**environment, "campaign_protocol_hash": protocol_hash}, as_of=as_of
        )
        raw = (
            await asyncio.to_thread(source.minutes, plan.symbol, plan.start_at, plan.end_at, plan.definition.data_feed)
            if result["status"] == ReplayStatus.COMPLETED
            else None
        )
        summary = await asyncio.to_thread(summarize_trial, result, raw, job["cost_bps"])
        row = {
            **{k: v for k, v in job.items() if k != "plan"},
            "run_id": result["run_id"],
            "plan_id": plan.identity,
            "summary": summary,
            "result_hash": await asyncio.to_thread(file_hash, folder / "result.json"),
        }
        await asyncio.to_thread(save_json_report, row, folder / "summary.json")
        rows.append(row)
        print(json.dumps({"completed": len(rows), "budget": len(jobs), "status": result["status"]}), flush=True)
    decisions = []
    for symbol in protocol["symbols"]:
        for hypothesis in protocol["hypotheses"]:
            cohort = [r for r in rows if r["symbol"] == symbol and r["hypothesis"] == hypothesis["id"]]
            primary, stressed = (
                [r["summary"] for r in cohort if r["cost_bps"] == protocol["triage"][key]]
                for key in ("primary_cost_bps", "stress_cost_bps")
            )
            decisions.append(
                {
                    "symbol": symbol,
                    "hypothesis": hypothesis["id"],
                    **triage(primary, stressed, protocol["triage"], expected_blocks=len(protocol["windows"])),
                }
            )
    await asyncio.to_thread(
        save_json_report,
        {
            "protocol_hash": protocol_hash,
            "rows": rows,
            "decisions": decisions,
            "source_receipts": source.receipts,
            "completed_at": datetime.now(UTC).isoformat(),
            "authorizes_promotion": False,
        },
        output / "result.json",
    )
    print(
        json.dumps(
            {
                "completed": len(rows),
                "advance": sum(d["advance_to_further_research"] for d in decisions),
                "authorizes_promotion": False,
            }
        ),
        flush=True,
    )


async def run_campaign(protocol_path: Path, output: Path):
    protocol = json.loads(await asyncio.to_thread(protocol_path.read_text))
    await asyncio.to_thread(campaign_jobs, protocol)
    config = load_config()
    environment = await asyncio.to_thread(research_environment)
    with session_source(config, protocol["feed"].split(":")[1]) as source:
        async with alpha_repository() as repository:
            await execute_campaign(protocol_path, output, repository, source, environment=environment)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("protocol", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(run_campaign(arguments.protocol, arguments.output))
