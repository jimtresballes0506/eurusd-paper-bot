"""Read-only Exness MT5 demo quotes. Never submits broker orders."""
import math
import time
from dataclasses import dataclass


class FeedSetupError(ValueError):
    """A setup/account change requiring human attention, not a retry."""


@dataclass(frozen=True)
class Settings:
    terminal_path: str
    login: int
    server: str
    symbol: str

    def __post_init__(self):
        if type(self.login) is not int or self.login <= 0:
            raise FeedSetupError('Enter the numeric DEMO account login in mt5-settings.json.')
        if not all(isinstance(v, str) and v.strip() for v in
                   (self.terminal_path, self.server, self.symbol)):
            raise FeedSetupError('Terminal path, demo server and exact EURUSD symbol are required.')

    def identity(self):
        return dict(provider='exness_mt5_demo', login=self.login,
                    server=self.server, symbol=self.symbol)


class MT5Feed:
    def __init__(self, api, settings):
        self.api, self.settings = api, settings

    def verify_account(self):
        terminal = self.api.terminal_info()
        account = self.api.account_info()
        if terminal is None or not terminal.connected or account is None:
            raise ConnectionError('MT5 is disconnected. Log in to your Exness demo terminal.')
        if account.trade_mode != self.api.ACCOUNT_TRADE_MODE_DEMO:
            raise FeedSetupError('MT5 must be connected to a DEMO account. Worker stopped.')
        if (account.login, account.server) != (self.settings.login, self.settings.server):
            raise FeedSetupError('MT5 account changed or does not match mt5-settings.json.')
        if 'exness' not in account.company.lower():
            raise FeedSetupError('The selected demo terminal is not an Exness account.')
        return account

    def connect(self):
        # Use the explicitly selected terminal and its saved login. No passwords in code.
        if not self.api.initialize(self.settings.terminal_path, timeout=10000):
            raise ConnectionError('Could not connect to the selected MT5 terminal.')
        self.verify_account()
        if not self.api.symbol_select(self.settings.symbol, True):
            raise FeedSetupError('Select the exact EURUSD symbol from MT5 Market Watch.')
        info = self.api.symbol_info(self.settings.symbol)
        if info is None:
            raise ConnectionError('MT5 has not supplied symbol information yet.')
        if (info.currency_base, info.currency_profit) != ('EUR', 'USD'):
            raise FeedSetupError('Only EUR/USD instruments are supported.')
        values = (info.trade_contract_size, info.volume_min, info.volume_step)
        if not all(math.isfinite(v) and v > 0 for v in values):
            raise FeedSetupError('MT5 supplied invalid contract or lot-size information.')
        return dict(symbol=self.settings.symbol,
                    broker_min_units=info.trade_contract_size * info.volume_min,
                    broker_unit_step=info.trade_contract_size * info.volume_step)

    def latest(self, now, previous_time=0):
        self.verify_account()
        tick = self.api.symbol_info_tick(self.settings.symbol)
        # Detect account switches that happened during the quote request as well.
        self.verify_account()
        if tick is None:
            return None
        t, bid, ask = tick.time_msc / 1000.0, tick.bid, tick.ask
        if not all(math.isfinite(x) for x in (t, bid, ask)):
            return None
        if t <= previous_time or not -2 <= now-t <= 15 or bid <= 0 or ask < bid:
            return None
        return t, bid, ask

    def quotes(self, log, previous_time=0, clock=time.time, sleep=time.sleep):
        connected, delay, last_wait_log = False, 2, 0
        try:
            while True:
                try:
                    if not connected:
                        details = self.connect()
                        log(dict(status='MT5_CONNECTED', **details))
                        connected = True
                    now = clock()
                    quote = self.latest(now, previous_time)
                    if quote is not None:
                        previous_time = quote[0]
                        delay = 2
                        yield quote
                    elif now-last_wait_log >= 60:
                        log(dict(status='WAITING_FOR_FRESH_MT5_QUOTE',
                                 hint='Check market hours, terminal connection and server clock.'))
                        last_wait_log = now
                    sleep(0.5)
                except ConnectionError:
                    self.api.shutdown()
                    connected = False
                    log(dict(status='MT5_RECONNECT', delay_seconds=delay))
                    sleep(delay)
                    delay = min(delay*2, 60)
        finally:
            self.api.shutdown()


def bind_identity(state, settings):
    identity = settings.identity()
    existing = state.get('feed_identity')
    if existing is None and state['last_time']:
        raise FeedSetupError('Existing ledger has another/unknown feed. Use a separate database.')
    if existing is not None and existing != identity:
        raise FeedSetupError('This ledger belongs to a different MT5 account or symbol.')
    state['feed_identity'] = identity
