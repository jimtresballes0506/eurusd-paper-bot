"""Run the $10 simulator using an already logged-in Windows MT5 demo terminal."""
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import sys
import time

from bot import Config, Engine, Store, report
from mt5_feed import FeedSetupError, MT5Feed, Settings, bind_identity


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--settings', default='mt5-settings.json')
    parser.add_argument('--config', default='config.json')
    parser.add_argument('--db', default='mt5-paper.db')
    parser.add_argument('--check', action='store_true', help='Check terminal only; do not write a ledger.')
    parser.add_argument('--require-postgres', action='store_true')
    args = parser.parse_args()
    Path('logs').mkdir(exist_ok=True)
    logger = logging.getLogger('paper-mt5')
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler('logs/mt5.log', maxBytes=2_000_000, backupCount=5, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    logger.addHandler(handler)
    logger.addHandler(logging.StreamHandler())
    def log(value):
        logger.info(json.dumps(value))
    store, quotes, api = None, None, None
    try:
        if sys.platform != 'win32':
            raise FeedSetupError('Run this worker on Windows with the MT5 desktop terminal.')
        import MetaTrader5 as api
        with open(args.settings, encoding='utf-8-sig') as f:
            settings = Settings(**json.load(f))
        with open(args.config, encoding='utf-8-sig') as f:
            cfg = Config(**json.load(f))
        feed = MT5Feed(api, settings)
        if args.check:
            details = feed.connect()
            # Symbol selection can need a moment to subscribe to quotes.
            quote = None
            for _ in range(10):
                quote = feed.latest(time.time())
                if quote is not None:
                    break
                time.sleep(0.5)
            log(dict(status='MT5_DEMO_CHECK_OK', **details,
                     fresh_quote=quote is not None, simulation_min_units=cfg.min_units,
                     note='No orders submitted. No ledger modified.'))
            return 0
        url = os.getenv('DATABASE_URL')
        if args.require_postgres and not url:
            raise FeedSetupError('DATABASE_URL is required in this mode; local fallback disabled.')
        store = Store(args.db, cfg, url)
        engine = Engine(cfg, store.load())
        bind_identity(engine.s, settings)
        store.save(engine.s, [])
        log(dict(status='PAPER_ONLY_START', storage='postgres' if url else 'local_sqlite',
                 initial_balance=cfg.initial_balance, min_units=cfg.min_units,
                 note='Internal simulation only; no trades appear in MT5.'))
        quotes = feed.quotes(log, previous_time=engine.s['last_time'])
        last_log = 0
        for t, bid, ask in quotes:
            events = engine.tick(t, bid, ask)
            store.save(engine.s, events)
            for event in events:
                log(event)
            if t-last_log >= 60:
                log(report(engine.s))
                last_log = t
        return 0
    except KeyboardInterrupt:
        log(dict(status='STOPPED', note='Saved state retained.'))
        return 0
    except FeedSetupError as error:
        log(dict(status='SETUP_REQUIRED', message=str(error)))
        return 2
    except Exception as error:
        # Avoid dumping MT5 account details, DB URLs or credentials in tracebacks.
        log(dict(status='FATAL', error_type=type(error).__name__,
                 hint='Check settings, dependencies, terminal and database. State retained.'))
        return 1
    finally:
        if quotes is not None:
            quotes.close()
        if api is not None:
            api.shutdown()
        if store is not None:
            store.close()


if __name__ == '__main__':
    raise SystemExit(main())
