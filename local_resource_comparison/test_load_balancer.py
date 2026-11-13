import unittest
import threading
from werkzeug.serving import make_server
import requests
from unittest.mock import patch, Mock
import local_load_balancer as lb

class TestLB(unittest.TestCase):
    def setUp(self):
        lb.healthy=list(lb.BACKENDS);lb.draining.clear();lb.inflight.update({b:0 for b in lb.BACKENDS});lb.index=0
        self.client=lb.app.test_client()
    def test_drained_backend_excluded_from_admission(self):
        self.client.post('/control/drain',json={'backend':lb.BACKENDS[0]})
        with patch.object(lb.http,'get',return_value=Mock(content=b'{}',status_code=200,headers={})) as get:
            self.assertEqual(self.client.get('/work').status_code,200)
            self.assertEqual(get.call_args.args[0],lb.BACKENDS[1]+'/work')
        self.assertTrue(all(v==0 for v in lb.inflight.values()))
    def test_failure_releases_inflight_without_retry(self):
        with patch.object(lb.http,'get',side_effect=lb.requests.Timeout('test')) as get:
            self.assertEqual(self.client.get('/work').status_code,502)
            self.assertEqual(get.call_count,1)
        self.assertTrue(all(v==0 for v in lb.inflight.values()))
    def test_real_health_responds_while_work_is_blocked(self):
        server=make_server('127.0.0.1',0,lb.app,threaded=True)
        server_thread=threading.Thread(target=server.serve_forever);server_thread.start()
        entered=threading.Event();release=threading.Event()
        result=[]
        def upstream(*args,**kwargs):
            entered.set();release.wait(3)
            return Mock(content=b'{}',status_code=200,headers={})
        base='http://127.0.0.1:'+str(server.server_port)
        def send_work():
            with requests.Session() as session:
                session.trust_env=False
                result.append(session.get(base+'/work',timeout=5).status_code)
        worker=threading.Thread(target=send_work)
        try:
            with patch.object(lb.http,'get',side_effect=upstream):
                worker.start();self.assertTrue(entered.wait(2))
                with requests.Session() as session:
                    session.trust_env=False
                    self.assertEqual(session.get(base+'/health',timeout=1).status_code,200)
                release.set();worker.join(5)
                self.assertEqual(result,[200])
        finally:
            release.set();worker.join(5);server.shutdown();server.server_close();server_thread.join()

    def test_rejects_unknown_control_target(self):
        self.assertEqual(self.client.post('/control/drain',json={'backend':'http://external'}).status_code,400)
if __name__=='__main__':unittest.main()
