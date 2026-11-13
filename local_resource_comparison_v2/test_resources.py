import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from resource_observer import summarize
from resource_accounting import child_cpu_seconds

class ResourceTests(unittest.TestCase):
    def test_summary(self):
        rows=[dict(monotonic=10,timestamp=100,cpu_user_seconds=1,cpu_system_seconds=2,rss_bytes=10*2**20,threads={'1':.2}),dict(monotonic=12,timestamp=102,cpu_user_seconds=1.1,cpu_system_seconds=2.1,rss_bytes=20*2**20,threads={'1':.3})]
        r=summarize(rows,{'1':'control'})
        self.assertAlmostEqual(r['process_cpu_seconds'],.2)
        self.assertAlmostEqual(r['process_mean_cpu_percent_one_core'],10)
        self.assertEqual(r['process_mean_rss_mib'],15)
        self.assertEqual(r['process_sampled_peak_rss_mib'],20)
        self.assertAlmostEqual(r['thread_cpu_seconds']['control'],.1)
    def test_observer_real_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'controller.jsonl').write_text('')
            target=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
            obs=subprocess.Popen([sys.executable,str(Path(__file__).with_name('resource_observer.py')),'--pid',str(target.pid),'--output',tmp],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            try:
                obs.stdin.write('{"command":"START"}\n');obs.stdin.flush()
                self.assertTrue(json.loads(obs.stdout.readline())['ok'])
                time.sleep(1.1)
                out,err=obs.communicate('{"command":"END"}\n',timeout=10)
                self.assertEqual(obs.returncode,0,err)
                self.assertTrue(json.loads(out)['ok'])
                r=json.loads(Path(tmp,'resource_summary.json').read_text())
                self.assertGreaterEqual(r['samples'],3)
                self.assertGreater(r['duration_seconds'],1)
                self.assertGreater(r['process_sampled_peak_rss_mib'],0)
            finally:
                if obs.poll() is None:obs.kill();obs.communicate()
                target.terminate();target.wait()
    @unittest.skipUnless(sys.platform=='win32','Windows handle accounting')
    def test_windows_child_cpu(self):
        with subprocess.Popen([sys.executable,'-c','sum(i*i for i in range(1000000))']) as p:
            p.wait();self.assertGreater(child_cpu_seconds(p),0)
if __name__=='__main__':unittest.main()
