"""Bounded read-only metadata adapters with immutable pre-parse source evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from alpaca.trading.enums import AssetClass
from alpaca.trading.requests import GetAssetsRequest

from agentic_trader.data.evidence import artifact_reference
from agentic_trader.data.symbol_directory import DIRECTORY_NAMES, MAX_METADATA_ROWS, parse_directory
from agentic_trader.storage.artifacts import save_json_report
from agentic_trader.transport.alpaca import BoundedTradingClient, ResponsePage


NASDAQ_DIRECTORY_ROOT = "https://www.nasdaqtrader.com/dynamic/SymDir"
MAX_METADATA_BYTES = 32 * 1024 * 1024


class EquityMetadataSource:
    def __init__(self, trading: BoundedTradingClient, http: httpx.Client):
        self.trading = trading
        self.http = http

    def assets(self, output: Path) -> list[dict]:
        pages: list[list[dict]] = []

        def retain(page: ResponsePage):
            if page.path != "/v2/assets" or page.method != "GET" or pages:
                raise ValueError("Unexpected equity metadata response")
            if len(json.dumps(page.response).encode()) > MAX_METADATA_BYTES:
                raise ValueError("Equity metadata byte budget exceeded")
            if not isinstance(page.response, list) or not 0 < len(page.response) <= MAX_METADATA_ROWS:
                raise ValueError("Invalid asset response cardinality")
            save_json_report(
                {
                    "requested_at": page.requested_at.isoformat(),
                    "received_at": page.received_at.isoformat(),
                    "path": page.path,
                    "response": page.response,
                },
                output / "alpaca-assets.json",
            )
            pages.append(page.response)

        with self.trading.observe_responses(retain):
            # No status filter: retain current inactive/nontradable assets and broker identity too.
            self.trading.get_all_assets(GetAssetsRequest(asset_class=AssetClass.US_EQUITY))
        if len(pages) != 1:
            raise ValueError("Bounded SDK response capture required")
        return pages[0]

    def directory(self, name: str, output: Path) -> dict:
        if name not in DIRECTORY_NAMES:
            raise ValueError("Unsupported directory")
        requested = datetime.now(UTC)
        chunks, size = [], 0
        with self.http.stream("GET", f"{NASDAQ_DIRECTORY_ROOT}/{name}.txt") as response:
            response.raise_for_status()
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_METADATA_BYTES:
                    raise ValueError("Directory byte budget exceeded")
                chunks.append(chunk)
        raw = b"".join(chunks)
        save_json_report(
            {
                "source": name,
                "requested_at": requested.isoformat(),
                "received_at": datetime.now(UTC).isoformat(),
                "raw_base64": base64.b64encode(raw).decode("ascii"),
                "content_sha256": hashlib.sha256(raw).hexdigest(),
            },
            output / f"{name}.json",
        )
        return parse_directory(raw.decode("utf-8-sig"), name)


def metadata_references(output: Path) -> dict:
    return {
        name: artifact_reference(path)
        for name in ("alpaca-assets", *DIRECTORY_NAMES)
        if (path := output / f"{name}.json").exists()
    }
