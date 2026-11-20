import copy
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from bot import Config, Engine
from mt5_feed import FeedSetupError, MT5Feed, Settings, bind_identity


class MT5Tests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings('C:\\MT5\\terminal64.exe', 12345, 'Exness-Demo', 'EURUSDm')
        self.api = Mock()
        self.api.ACCOUNT_TRADE_MODE_DEMO = 0
        self.api.initialize.return_value = True
        self.api.terminal_info.return_value = NS(connected=True)
        self.account = NS(login=12345, server='Exness-Demo', trade_mode=0, company='Exness Technologies Ltd')
        self.api.account_info.return_value = self.account
        self.api.symbol_select.return_value = True
        self.api.symbol_info.return_value = NS(currency_base='EUR', currency_profit='USD',
            trade_contract_size=100000, volume_min=.01, volume_step=.01)
        self.api.symbol_info_tick.return_value = NS(time_msc=1000000, bid=1.1, ask=1.1001)
        self.feed = MT5Feed(self.api, self.settings)

    def test_connect_checks_contract_and_uses_explicit_terminal(self):
        details = self.feed.connect()
        self.assertEqual(details['broker_min_units'], 1000)
        self.api.initialize.assert_called_once_with(self.settings.terminal_path, timeout=10000)
        self.api.order_send.assert_not_called()

    def test_live_wrong_account_wrong_server_and_broker_refused(self):
        for change in ({'trade_mode': 2}, {'login': 999}, {'server': 'Other'}, {'company': 'Other'}):
            self.api.account_info.return_value = NS(**(vars(self.account) | change))
            with self.assertRaises(FeedSetupError):
                self.feed.connect()

    def test_wrong_instrument_refused(self):
        self.api.symbol_info.return_value.currency_base = 'GBP'
        with self.assertRaises(FeedSetupError):
            self.feed.connect()

    def test_stale_future_duplicate_and_bad_quotes_ignored(self):
        for tick, now, previous in [
            (NS(time_msc=1000000, bid=1.1, ask=1.1001), 1016, 0),
            (NS(time_msc=1003000, bid=1.1, ask=1.1001), 1000, 0),
            (NS(time_msc=1000000, bid=1.1, ask=1.1001), 1000, 1000),
            (NS(time_msc=1000000, bid=1.2, ask=1.1), 1000, 0),
            (NS(time_msc=1000000, bid=float('nan'), ask=1.1), 1000, 0),
            (NS(time_msc=1000000, bid=0, ask=0), 1000, 0),
        ]:
            self.api.symbol_info_tick.return_value = tick
            self.assertIsNone(self.feed.latest(now, previous))

    def test_account_switch_during_read_refused(self):
        self.api.account_info.side_effect = [self.account, NS(**(vars(self.account) | {'trade_mode': 2}))]
        with self.assertRaises(FeedSetupError):
            self.feed.latest(1000)

    def test_disconnect_does_not_return_cached_quote(self):
        self.api.terminal_info.return_value.connected = False
        with self.assertRaises(ConnectionError):
            self.feed.latest(1000)
        self.api.symbol_info_tick.assert_not_called()

    def test_reconnect_recovers_and_closes_api(self):
        self.api.initialize.side_effect = [False, True]
        sleep, log = Mock(), Mock()
        quotes = self.feed.quotes(log, clock=lambda: 1000, sleep=sleep)
        self.assertEqual(next(quotes), (1000, 1.1, 1.1001))
        sleep.assert_called_with(2)
        quotes.close()
        self.assertGreaterEqual(self.api.shutdown.call_count, 2)
        self.api.order_send.assert_not_called()

    def test_ledger_identity_cannot_mix_accounts(self):
        state = Engine(Config()).s
        bind_identity(state, self.settings)
        saved = copy.deepcopy(state)
        bind_identity(state, self.settings)
        self.assertEqual(state, saved)
        other = Settings('C:\\MT5\\terminal64.exe', 54321, 'Exness-Demo', 'EURUSDm')
        with self.assertRaises(FeedSetupError):
            bind_identity(state, other)
        legacy = Engine(Config()).s
        legacy['last_time'] = 1000
        with self.assertRaises(FeedSetupError):
            bind_identity(legacy, self.settings)

    def test_missing_settings_rejected(self):
        with self.assertRaises(FeedSetupError):
            Settings('', 12345, 'Exness-Demo', 'EURUSDm')
        with self.assertRaises(FeedSetupError):
            Settings('terminal64.exe', '12345', 'Exness-Demo', 'EURUSDm')


if __name__ == '__main__':
    unittest.main()
