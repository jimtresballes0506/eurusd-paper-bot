"""EUR/USD paper execution only. There is deliberately no order API client."""
import argparse
import csv
import json
import math
import os
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.parse import quote

PIP = 0.0001


def stamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def utc(t):
    return datetime.fromtimestamp(t, timezone.utc)


@dataclass(frozen=True)
class Config:
    initial_balance: float = 10.0
    risk_fraction: float = 0.01
    daily_loss_fraction: float = 0.03
    max_drawdown: float = 0.20
    leverage: float = 30.0
    margin_fraction: float = 0.50
    max_trades: int = 3
    min_units: int = 1
    unit_step: int = 1
    max_spread_pips: float = 1.5
    slippage_pips: float = 0.2
    commission_per_million_per_side: float = 0.0
    min_stop_pips: float = 5.0
    reward_ratio: float = 1.5
    start_hour_utc: int = 7
    end_hour_utc: int = 16
    max_hold_seconds: int = 3600
    max_quote_gap_seconds: int = 30

    def __post_init__(self):
        values = asdict(self)
        if any(not math.isfinite(v) for v in values.values()):
            raise ValueError('Configuration must be finite')
        if self.initial_balance <= 0 or self.leverage < 1:
            raise ValueError('Invalid balance or leverage')
        if not all(0 < v < 1 for v in (self.risk_fraction, self.daily_loss_fraction, self.max_drawdown, self.margin_fraction)):
            raise ValueError('Risk fractions must be between zero and one')
        if self.min_units < 1 or self.unit_step < 1 or self.max_trades < 1:
            raise ValueError('Invalid size or trade limit')
        if not 0 <= self.start_hour_utc < self.end_hour_utc <= 23:
            raise ValueError('Invalid UTC session')
        if min(self.min_stop_pips, self.reward_ratio, self.max_spread_pips, self.max_hold_seconds, self.max_quote_gap_seconds) <= 0:
            raise ValueError('Limits must be positive')
        if min(self.slippage_pips, self.commission_per_million_per_side) < 0:
            raise ValueError('Costs cannot be negative')


class Engine:
    def __init__(self, cfg, state=None):
        self.c = cfg
        self.s = state or dict(initial_balance=cfg.initial_balance, balance=cfg.initial_balance, peak=cfg.initial_balance,
            day='', day_equity=cfg.initial_balance, day_halted=False, halted=False,
            trades_today=0, position=None, last_time=0, bars=[], bar=None,
            last_equity=cfg.initial_balance, closed_trades=0, wins=0, gross_profit=0.0,
            gross_loss=0.0, status='WARMING_UP')
        self.events = []

    def equity(self, bid, ask):
        p = self.s['position']
        if not p:
            return self.s['balance']
        price = (bid if p['side'] == 1 else ask) - p['side'] * self.c.slippage_pips * PIP
        return self.s['balance'] + p['side'] * (price - p['entry']) * p['units'] - p['fee']

    def close(self, t, bid, ask, reason):
        p = self.s['position']
        price = (bid if p['side'] == 1 else ask) - p['side'] * self.c.slippage_pips * PIP
        gross = p['side'] * (price - p['entry']) * p['units']
        net = gross - 2 * p['fee']
        self.s['balance'] += gross - p['fee']  # Entry fee already deducted.
        self.s['closed_trades'] += 1
        self.s['wins'] += int(net > 0)
        self.s['gross_profit'] += max(net, 0)
        self.s['gross_loss'] += max(-net, 0)
        self.events.append(dict(type='CLOSE', time=t, **p, exit=price, net_pnl=net,
                                reason=reason, balance=self.s['balance']))
        self.s['position'] = None

    def signal(self):
        b = self.s['bars']
        if len(b) < 50:
            return 0, 0
        closes = [x['close'] for x in b]
        fast, slow = sum(closes[-20:]) / 20, sum(closes[-50:]) / 50
        prev = b[-11:-1]
        side = 1 if fast > slow and closes[-1] > max(x['high'] for x in prev) else 0
        if fast < slow and closes[-1] < min(x['low'] for x in prev):
            side = -1
        atr = sum(max(b[i]['high'] - b[i]['low'], abs(b[i]['high'] - b[i-1]['close']),
                      abs(b[i]['low'] - b[i-1]['close'])) for i in range(len(b)-14, len(b))) / 14
        return side, max(1.5 * atr, self.c.min_stop_pips * PIP)

    def enter(self, t, bid, ask, side, distance):
        c, s = self.c, self.s
        entry = (ask if side == 1 else bid) + side * c.slippage_pips * PIP
        fee_unit = c.commission_per_million_per_side / 1_000_000
        risk = s['balance'] * c.risk_fraction
        risk_units = risk / (distance + c.slippage_pips * PIP + 2 * fee_unit)
        margin_units = s['balance'] * c.margin_fraction / (entry / c.leverage + fee_unit)
        units = math.floor(min(risk_units, margin_units) / c.unit_step) * c.unit_step
        if units < c.min_units:
            s['status'] = 'SKIP_MINIMUM_SIZE'
            return
        fee = units * fee_unit
        p = dict(side=side, units=units, entry=entry, stop=entry-side*distance,
                 target=entry+side*distance*c.reward_ratio, opened=t, fee=fee)
        s['balance'] -= fee
        s['position'] = p
        s['trades_today'] += 1
        s['status'] = 'POSITION_OPEN'
        self.events.append(dict(type='OPEN', time=t, **p, modeled_stop_risk=units*(distance+c.slippage_pips*PIP+2*fee_unit)))

    def tick(self, t, bid, ask):
        self.events = []
        s, c = self.s, self.c
        if not all(math.isfinite(v) for v in (t, bid, ask)) or bid <= 0 or ask < bid:
            raise ValueError('Invalid quote')
        if t <= s['last_time']:
            return []
        d = utc(t)
        gap = bool(s['last_time'] and t-s['last_time'] > c.max_quote_gap_seconds)
        day = d.date().isoformat()
        if day != s['day']:
            s.update(day=day, day_equity=self.equity(bid, ask), day_halted=False, trades_today=0)
        s['last_time'] = t
        # Never fabricate fills during an outage. Close at first observed executable quote.
        closed = False
        if s['position']:
            p = s['position']
            mark = bid if p['side'] == 1 else ask
            reason = None
            if gap:
                reason = 'DATA_GAP_FIRST_AVAILABLE_QUOTE'
            elif d.weekday() >= 5 or not c.start_hour_utc <= d.hour < c.end_hour_utc:
                reason = 'SESSION_END'
            elif p['side'] * (mark-p['stop']) <= 0:
                reason = 'STOP_LOSS'
            elif p['side'] * (mark-p['target']) >= 0:
                reason = 'TAKE_PROFIT'
            elif t-p['opened'] >= c.max_hold_seconds:
                reason = 'TIME_EXIT'
            if reason:
                self.close(t, bid, ask, reason)
                closed = True
        eq = self.equity(bid, ask)
        s['peak'] = max(s['peak'], eq)
        s['day_halted'] |= eq <= s['day_equity'] * (1-c.daily_loss_fraction)
        s['halted'] |= eq <= s['peak'] * (1-c.max_drawdown) or eq <= 0
        if (s['day_halted'] or s['halted']) and s['position']:
            self.close(t, bid, ask, 'RISK_HALT')
            eq = s['balance']
            closed = True
        s['last_equity'] = eq
        bucket, mid = int(t // 300) * 300, (bid+ask)/2
        completed = False
        if gap:
            s['bars'], s['bar'] = [], None
        if s['bar'] and s['bar']['time'] != bucket:
            s['bars'] = (s['bars'] + [s['bar']])[-60:]
            completed = True
            s['bar'] = None
        if s['bar'] is None:
            s['bar'] = dict(time=bucket, high=mid, low=mid, close=mid)
            # Initial/gap bar may be partial; discard it on next rollover.
            if gap or len(s['bars']) == 0 and not completed:
                s['bar']['partial'] = t-bucket > 5
        else:
            s['bar'].update(high=max(s['bar']['high'], mid), low=min(s['bar']['low'], mid), close=mid)
        if completed and s['bars'][-1].get('partial'):
            s['bars'].pop()
            completed = False
        s['status'] = 'POSITION_OPEN' if s['position'] else 'WAITING_FOR_SIGNAL'
        if s['halted'] or s['day_halted']:
            s['status'] = 'DRAWDOWN_HALT' if s['halted'] else 'DAILY_LOSS_HALT'
        elif d.weekday() >= 5 or not c.start_hour_utc <= d.hour < c.end_hour_utc:
            s['status'] = 'OUTSIDE_SESSION'
        elif len(s['bars']) < 50:
            s['status'] = 'WARMING_UP'
        elif s['trades_today'] >= c.max_trades:
            s['status'] = 'DAILY_TRADE_LIMIT'
        elif (ask-bid)/PIP > c.max_spread_pips:
            s['status'] = 'SPREAD_TOO_WIDE'
        elif completed and not s['position'] and not closed:
            side, distance = self.signal()
            if side:
                self.enter(t, bid, ask, side, distance)
        s['last_equity'] = self.equity(bid, ask)
        return self.events


class Store:
    """One atomic state + journal commit per quote; one writer across workers."""
    def __init__(self, path, cfg, pg_url=None):
        self.pg = bool(pg_url)
        self.cfg = cfg
        self.placeholder = '%s' if self.pg else '?'
        if self.pg:
            import psycopg
            self.db = psycopg.connect(pg_url, sslmode='require', connect_timeout=15, autocommit=True)
            row = self.db.execute('SELECT pg_try_advisory_lock(710320261119)').fetchone()
            if not row[0]:
                self.db.close()
                raise RuntimeError('Another worker already owns this paper account')
        else:
            # Holding this separate OS lock also protects between transactions.
            self.lock = open(path + '.lock', 'a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.lock.seek(0, os.SEEK_END)
                    if self.lock.tell() == 0:
                        self.lock.write(b'0')
                        self.lock.flush()
                    self.lock.seek(0)
                    msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                self.lock.close()
                raise BlockingIOError('Cannot acquire the paper-account writer lock') from error
            self.db = sqlite3.connect(path)
            self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS eurusd_state (id INTEGER PRIMARY KEY, config TEXT NOT NULL, payload TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS eurusd_events (event_key TEXT PRIMARY KEY, payload TEXT NOT NULL)')
        self.db.commit()

    def load(self):
        row = self.db.execute('SELECT config, payload FROM eurusd_state WHERE id=1').fetchone()
        if not row:
            return None
        if json.loads(row[0]) != asdict(self.cfg):
            raise ValueError('Config differs from saved account. Restore config or use a separate database.')
        return json.loads(row[1])

    def save(self, state, events):
        p = self.placeholder
        with (self.db.transaction() if self.pg else self.db):
            self.db.execute(f'INSERT INTO eurusd_state VALUES (1,{p},{p}) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',
                (json.dumps(asdict(self.cfg)), json.dumps(state)))
            for e in events:
                key = f"{e['time']:.6f}:{e['type']}"
                self.db.execute(f'INSERT INTO eurusd_events VALUES ({p},{p}) ON CONFLICT(event_key) DO NOTHING', (key,json.dumps(e)))

    def close(self):
        self.db.close()
        if not self.pg:
            self.lock.close()


def feed():
    import requests
    token, account = os.environ.get('OANDA_TOKEN'), os.environ.get('OANDA_ACCOUNT_ID')
    if not token or not account:
        raise RuntimeError('Set OANDA_TOKEN and OANDA_ACCOUNT_ID in your private environment')
    url = 'https://stream-fxpractice.oanda.com/v3/accounts/' + quote(account, safe='') + '/pricing/stream'
    delay = 2
    while True:
        try:
            with requests.get(url, params={'instruments':'EUR_USD'}, headers={'Authorization':'Bearer '+token},
                              stream=True, timeout=(15,20), allow_redirects=False) as r:
                if r.status_code in (401,403,404):
                    raise RuntimeError(f'Practice feed refused access (HTTP {r.status_code})')
                r.raise_for_status()
                for line in r.iter_lines(chunk_size=1):
                    if not line:
                        continue
                    x = json.loads(line)
                    if x.get('type') != 'PRICE' or x.get('status') != 'tradeable':
                        continue
                    t = stamp(x['time'])
                    if not -2 <= time.time()-t <= 15:
                        continue
                    bid = float(x['bids'][0]['price'])
                    ask = float(x['asks'][0]['price'])
                    delay = 2
                    yield t, bid, ask
        except (requests.RequestException, ValueError, KeyError, IndexError):
            # Do not print exception payloads, headers, URLs, or secrets.
            print(json.dumps({'status':'FEED_RECONNECT','delay_seconds':delay}), flush=True)
        time.sleep(delay)
        delay = min(60, delay*2)


def report(state):
    n = state['closed_trades']
    return dict(balance=round(state['balance'],6), equity=round(state['last_equity'],6),
        net_pnl=round(state['last_equity']-state['initial_balance'],6), closed_trades=n,
        win_rate=state['wins']/n if n else None,
        profit_factor=state['gross_profit']/state['gross_loss'] if state['gross_loss'] else None,
        status=state['status'], open_position=state['position'], last_quote_utc=utc(state['last_time']).isoformat() if state['last_time'] else None)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['run','replay','status'])
    parser.add_argument('--csv', help='Real tick CSV: time,bid,ask. Time must include UTC offset.')
    parser.add_argument('--db', default='paper.db')
    parser.add_argument('--config', default='config.json')
    args = parser.parse_args()
    cfg = Config(**json.load(open(args.config)))
    # Replay intentionally cannot mutate the cloud paper account.
    url = os.getenv('DATABASE_URL') if args.command != 'replay' else None
    if args.command == 'status':
        if url:
            import psycopg
            db = psycopg.connect(url, sslmode='require', connect_timeout=15)
        else:
            db = sqlite3.connect('file:' + args.db + '?mode=ro', uri=True)
        try:
            row = db.execute('SELECT payload FROM eurusd_state WHERE id=1').fetchone()
            print(json.dumps(report(json.loads(row[0])), indent=2) if row else 'No saved account')
        finally:
            db.close()
        return
    store = Store(args.db, cfg, url)
    try:
        engine = Engine(cfg, store.load())
        if args.command == 'status':
            print(json.dumps(report(engine.s), indent=2))
            return
        store.save(engine.s, [])
        if args.command == 'replay':
            if not args.csv:
                raise ValueError('replay requires --csv')
            def rows():
                with open(args.csv, newline='') as f:
                    for r in csv.DictReader(f):
                        if datetime.fromisoformat(r['time'].replace('Z','+00:00')).tzinfo is None:
                            raise ValueError('CSV timestamps require timezone')
                        yield stamp(r['time']), float(r['bid']), float(r['ask'])
            quotes = rows()
        else:
            quotes = feed()
        last_log = 0
        for t, bid, ask in quotes:
            events = engine.tick(t,bid,ask)
            store.save(engine.s, events)  # A DB failure exits; never continue unsaved.
            for e in events:
                print(json.dumps(e), flush=True)
            if t-last_log >= 60:
                print(json.dumps(report(engine.s)), flush=True)
                last_log = t
        print(json.dumps(report(engine.s), indent=2))
    finally:
        store.close()


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped; state saved. An open paper position is resolved on the next quote after restart.')
    except Exception as error:
        # Generic fatal message prevents credentials appearing in DB exception strings.
        print(f'FATAL {type(error).__name__}: worker stopped; verify config, feed access and database connectivity.', flush=True)
        raise SystemExit(1)
