import json
from pathlib import Path
import queue
import tempfile
import threading
import time
import unittest
from controller import Experiment, STOP_ACTION
import test_controller

class BlockingTests(unittest.TestCase):
    def test_control_wait_has_no_timeout_and_exit_wakes_it(self):
        class Notice:
            def __init__(self):self.event=threading.Event();self.calls=[]
            def wait(self,*args):self.calls.append(args);return self.event.wait(*args)
            def set(self):self.event.set()
            def clear(self):self.event.clear()
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(test_controller.TestMatched().args(tmp,'event'),lambda:(50,['app1']),lambda e:None)
            ex.notice=Notice();ex.run()
            self.assertEqual(ex.notice.calls,[()])
            self.assertEqual(ex.counts.get('notification_received',0),0)
    def test_pending_action_finishes_before_stop_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            args=test_controller.TestMatched().args(tmp,'event');args.seconds=.05
            finished=threading.Event()
            def act(e):time.sleep(.1);finished.set()
            ex=Experiment(args,lambda:(80,['app1']),act);ex.run()
            self.assertTrue(finished.is_set())
            self.assertEqual(ex.counts.get('action_finished'),1)
            self.assertEqual(ex.actions.unfinished_tasks,0)
    def test_monitor_failure_wakes_control_and_worker(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(test_controller.TestMatched().args(tmp,'event'),lambda:(50,['app1']),lambda e:None)
            def broken():raise RuntimeError('monitor crash')
            ex.monitor=broken
            with self.assertRaisesRegex(RuntimeError,'monitor crash'):ex.run()
    def test_worker_get_has_no_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(test_controller.TestMatched().args(tmp,'event'),lambda:(50,['app1']),lambda e:None)
            class StopQueue(queue.Queue):
                def get(self,*args,**kwargs):
                    self.arguments=(args,kwargs);return super().get(*args,**kwargs)
            ex.actions=StopQueue(1);ex.run()
            self.assertEqual(ex.actions.arguments,((),{}))
if __name__=='__main__':unittest.main()
