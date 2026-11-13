import tempfile
import unittest
from unittest.mock import patch,Mock
import test_controller
from controller import Experiment,URLS
import local_load_balancer as lb

class TestDrainLifecycle(unittest.TestCase):
    def test_scale_in_never_undrains_stopped_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(test_controller.TestMatched().args(tmp,'polling'))
            try:
                with patch('controller.running',side_effect=[['app1','app2'],['app1']]),patch('controller.docker') as docker,patch('controller.http.post',return_value=Mock()) as post,patch('controller.http.get',return_value=Mock(json=lambda:{'inflight':{URLS['app2']:0}})):
                    ex.execute({'names':['app1','app2'],'action':'scale_in','action_id':'test'})
                    self.assertEqual(len(post.call_args_list),1)
                    self.assertTrue(post.call_args.args[0].endswith('/drain'))
                    docker.assert_called_once_with('stop','app2')
            finally:ex.file.close()
    def test_scale_out_undrains_only_after_health(self):
        with tempfile.TemporaryDirectory() as tmp:
            ex=Experiment(test_controller.TestMatched().args(tmp,'event'));order=[]
            def get(url,**kw):
                order.append(('get',url))
                return Mock(status_code=200,json=lambda:{'status':'healthy'} if ':5001/' in url else {'backends':[URLS['app1'],URLS['app2']]})
            def post(url,**kw):order.append(('post',url));return Mock()
            try:
                with patch('controller.running',side_effect=[['app1'],['app1','app2']]),patch('controller.docker',side_effect=lambda *a:order.append(('docker',a))),patch('controller.http.get',side_effect=get),patch('controller.http.post',side_effect=post):
                    ex.execute({'names':['app1'],'action':'scale_out','action_id':'test'})
                labels=[x[1] for x in order]
                self.assertLess(labels.index(URLS['app2']+'/health'),labels.index('http://127.0.0.1:8000/control/undrain'))
                self.assertEqual(ex.counts['application_ready'],1)
            finally:ex.file.close()
    def test_stale_healthy_result_cannot_route_to_drained_app2(self):
        lb.healthy=[URLS['app1'],URLS['app2']];lb.draining={URLS['app2']};lb.index=0
        client=lb.app.test_client()
        with patch.object(lb.http,'get',return_value=Mock(status_code=200,content=b'{}',headers={})) as get:
            for _ in range(10):self.assertEqual(client.get('/work').status_code,200)
            self.assertTrue(all(c.args[0]==URLS['app1']+'/work' for c in get.call_args_list))
        lb.draining.clear()
if __name__=='__main__':unittest.main()
