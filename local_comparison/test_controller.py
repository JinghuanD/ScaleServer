import argparse
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from controller import Confirmation, Experiment

class TestMatched(unittest.TestCase):
    def test_confirmation_resets_on_change_and_invalid(self):
        c=Confirmation(high_n=2,low_n=2)
        self.assertIsNone(c.update(80,1)); self.assertEqual(c.update(80,2),'scale_out')
        self.assertIsNone(c.update(None,3)); self.assertIsNone(c.update(80,4))
        self.assertIsNone(c.update(20,5)); self.assertEqual(c.update(20,6),'scale_in')
        self.assertIsNone(c.update(50,7))
    def args(self,tmp,arm):
        return argparse.Namespace(mode=arm,run_id=arm,output=tmp,seconds=.35,sample_period=.01,poll_period=.03,max_gap=.1,cooldown=.03,action_delay=0,high=70,low=30,high_samples=2,low_samples=2)
    def test_both_arms_monitor_during_slow_action(self):
        for arm in ('polling','event'):
            with tempfile.TemporaryDirectory() as tmp:
                changed=threading.Event()
                def sample():return (50 if changed.is_set() else 80), ['app1','app2'] if changed.is_set() else ['app1']
                def action(e):time.sleep(.08);changed.set()
                ex=Experiment(self.args(tmp,arm),sample,action);ex.run()
                r=[json.loads(l) for l in (Path(tmp)/arm/'controller.jsonl').read_text().splitlines()]
                self.assertTrue(any(x['kind']=='sample' and x['overlaps_action'] for x in r))
                self.assertEqual(sum(x['kind']=='action_started' for x in r),1)
                self.assertTrue(changed.is_set())
                self.assertEqual(any(x['kind']=='state_query' for x in r),arm=='polling')
    def test_stable_event_has_samples_without_evaluations(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(self.args(tmp,'event'),lambda:(50,['app1']),lambda e:None);ex.run()
            self.assertGreater(ex.counts.get('sample',0),5)
            self.assertEqual(ex.counts.get('controller_evaluation',0),0)
    def test_failed_action_is_visible_and_stops_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(e):raise RuntimeError('injected')
            ex=Experiment(self.args(tmp,'event'),lambda:(80,['app1']),fail)
            with self.assertRaises(RuntimeError):ex.run()
            self.assertFalse(ex.pending)
    def test_stale_state_cannot_create_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(self.args(tmp,'polling'),lambda:(80,['app1']),lambda e:None)
            ex.latest=dict(action='scale_out',version=1,observed=time.monotonic()-10)
            ex.evaluate('test');self.assertFalse(ex.pending);ex.file.close()
if __name__=='__main__':unittest.main()
