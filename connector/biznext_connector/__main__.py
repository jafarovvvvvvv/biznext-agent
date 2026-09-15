"""BizNext Connector CLI.

    python -m biznext_connector check                 # 1C reachable? all mapped objects published? (no data sent)
    python -m biznext_connector once [--full|--incremental]
    python -m biznext_connector run                   # service: sync every SYNC_INTERVAL_MINUTES
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys
from datetime import datetime, timezone

from biznext_connector import VERSION
from biznext_connector.biznext import BizNextClient
from biznext_connector.config import Settings
from biznext_connector.contract import Contract
from biznext_connector.onec.adapter import OneCAdapter
from biznext_connector.onec.mapping import Mapping
from biznext_connector.onec.odata import ODataClient
from biznext_connector.sync import SyncRunner

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if len(s) >= 4]

    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "level": record.levelname,
                   "logger": record.name, "message": record.getMessage()}
        payload.update({k: v for k, v in record.__dict__.items() if k not in _RESERVED})
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        text = json.dumps(payload, ensure_ascii=False, default=str)
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text


def build(settings: Settings) -> tuple[Contract, OneCAdapter, BizNextClient]:
    contract = Contract.load(settings.contract_path)
    mapping = Mapping.load(settings.mapping_path)
    client = ODataClient(settings.onec_url, settings.onec_user, settings.onec_password, page_size=settings.page_size,
                         timeout=settings.http_timeout_seconds, verify=settings.verify_tls)
    adapter = OneCAdapter(client, mapping, contract, entities=settings.entities or None, excluded=settings.excluded())
    biznext = BizNextClient(settings.biznext_url, settings.token, timeout=settings.http_timeout_seconds,
                            verify=settings.verify_tls)
    return contract, adapter, biznext


async def main_async(args: argparse.Namespace, settings: Settings) -> int:
    contract, adapter, biznext = build(settings)
    try:
        if args.command == "check":
            result = await adapter.check()
            result["description"] = adapter.describe()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["ok"] else 1
        runner = SyncRunner(settings, contract, adapter, biznext)
        if args.command == "once":
            kind = "full" if args.full else "incremental" if args.incremental else "auto"
            result = await runner.run_once(kind)
            print(json.dumps(result, ensure_ascii=False))
            return 0 if result["status"] == "succeeded" else 1
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        logging.getLogger("biznext_connector").info("connector started", extra={"version": VERSION,
                                                                                 "entities": adapter.entities()})
        await runner.loop(stop)
        return 0
    finally:
        await adapter.close()
        await biznext.close()


def main() -> int:
    parser = argparse.ArgumentParser(prog="biznext_connector", description="BizNext read-only data connector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    once = sub.add_parser("once")
    group = once.add_mutually_exclusive_group()
    group.add_argument("--full", action="store_true")
    group.add_argument("--incremental", action="store_true")
    sub.add_parser("run")
    args = parser.parse_args()

    settings = Settings.from_env()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter(settings.secrets()))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    missing = settings.missing()
    if args.command == "check":
        missing = [m for m in missing if m not in ("BIZNEXT_URL", "CONNECTOR_TOKEN")]
    if missing:
        print(f"missing configuration: {', '.join(missing)}", file=sys.stderr)
        return 2
    return asyncio.run(main_async(args, settings))


if __name__ == "__main__":
    raise SystemExit(main())
