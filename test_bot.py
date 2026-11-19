import json
import os
import tempfile
import unittest
from dataclasses import asdict, replace
from bot import Config, Engine, Store, stamp, PIP

T = stamp('2026-11-19T10:00:00Z')

class BotTests(unittest.TestCase):
    def engine(self, **changes):
        e = Engine(replace(Config(), **changes))
        e.tick(T, 1.1, 1.1001)
        return e

    def test_starting_balance(self):
        self.assertEqual(self.engine().s['balance'], 10)

    def test_long_spread_slippage_and_fees(self):
        e = self.engine(commission_per_million_per_side=35)
        e.enter(T, 1.1, 1.1001, 1, .001)
        p = e.s['position'].copy()
        self.assertAlmostEqual(p['entry'], 1.10012)
        e.close(T+1, 1.1, 1.1001, 'TEST')
        expected = p['units'] * (-.00014 - .00007)
        self.assertAlmostEqual(e.s['balance'], 10+expected)
        self.assertAlmostEqual(e.events[-1]['net_pnl'], expected)

    def test_short_stop_uses_ask(self):
        e = self.engine()
        e.enter(T, 1.1, 1.1001, -1, .001)
        stop = e.s['position']['stop']
        e.tick(T+1, stop-.0002, stop+.00001)
        self.assertIsNone(e.s['position'])
        self.assertEqual(e.events[0]['reason'], 'STOP_LOSS')

    def test_gap_does_not_fill_at_stale_stop(self):
        e = self.engine()
        e.enter(T, 1.1, 1.1001, 1, .001)
        e.tick(T+60, 1.09, 1.0901)
        self.assertAlmostEqual(e.events[0]['exit'],1.09-.2*PIP)
        self.assertEqual(e.events[0]['reason'], 'DATA_GAP_FIRST_AVAILABLE_QUOTE')
        self.assertFalse(e.s['bars'])

    def test_duplicate_quote_idempotence(self):
        e = self.engine()
        before = json.dumps(e.s, sort_keys=True)
        e.tick(T, 1.0, 1.0001)
        self.assertEqual(json.dumps(e.s, sort_keys=True),before)

    def test_size_risk_margin_and_minimum(self):
        e = self.engine()
        e.enter(T,1.1,1.1001,1,.001)
        p=e.s['position']
        self.assertLessEqual(p['units']*(.001+.2*PIP),.1)
        self.assertLessEqual(p['units']*p['entry']/30,5)
        e=self.engine(min_units=1000,unit_step=1000)
        e.enter(T,1.1,1.1001,1,.001)
        self.assertIsNone(e.s['position'])

    def test_daily_and_permanent_halts(self):
        e=self.engine()
        e.s['balance']=9.6
        e.tick(T+1,1.1,1.1001)
        self.assertTrue(e.s['day_halted'])
        e.tick(T+86400,1.1,1.1001)
        self.assertFalse(e.s['day_halted'])
        e.s['balance']=7.9
        e.tick(T+86401,1.1,1.1001)
        self.assertTrue(e.s['halted'])
        e.tick(T+172800,1.1,1.1001)
        self.assertTrue(e.s['halted'])

    def test_session_closes_at_16_utc(self):
        e=self.engine()
        t=stamp('2026-11-19T15:59:59Z')
        e.tick(t,1.1,1.1001)
        e.enter(t,1.1,1.1001,1,.001)
        e.tick(t+1,1.1,1.1001)
        self.assertEqual(e.events[0]['reason'],'SESSION_END')

    def test_restart_and_config_guard(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'paper.db')
            e=self.engine()
            e.enter(T,1.1,1.1001,1,.001)
            st=Store(p,e.c)
            st.save(e.s,e.events)
            st.save(e.s,e.events)
            self.assertEqual(st.db.execute('SELECT count(*) FROM eurusd_events').fetchone()[0],1)
            st.close()
            st=Store(p,e.c)
            self.assertEqual(st.load(),e.s)
            st.close()
            st=Store(p,replace(e.c,risk_fraction=.02))
            with self.assertRaises(ValueError): st.load()
            st.close()

    def test_second_writer_blocked(self):
        with tempfile.TemporaryDirectory() as d:
            p=os.path.join(d,'paper.db')
            st=Store(p,Config())
            with self.assertRaises(BlockingIOError): Store(p,Config())
            st.close()

    def test_invalid_quotes(self):
        e=self.engine()
        for bid,ask in [(float('nan'),1.1),(1.2,1.1),(0,0)]:
            with self.assertRaises(ValueError): e.tick(T+1,bid,ask)

    def test_closed_candle_signal_and_limits(self):
        # Synthetic monotonic ticks test mechanics, not profitability.
        e=Engine(Config())
        start=stamp('2026-11-19T07:00:00Z')
        opens=[]
        for i in range(0, 8*3600, 5):
            price=1.1+i*.0000005
            opens.extend(x for x in e.tick(start+i,price,price+.0001) if x['type']=='OPEN')
        self.assertTrue(opens)
        self.assertLessEqual(len(opens),3)
        self.assertGreaterEqual(opens[0]['time'],start+50*300)
        self.assertTrue(all((x['time']-start)%300==0 for x in opens))

if __name__=='__main__': unittest.main()
