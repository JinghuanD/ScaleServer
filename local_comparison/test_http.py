import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import local_http as h

class TestLocalTransport(unittest.TestCase):
    def test_real_loopback_bypasses_proxy_and_retries_startup_only(self):
        class Handler(BaseHTTPRequestHandler):
            calls=0
            def log_message(self,*args):pass
            def do_GET(self):
                Handler.calls+=1
                if Handler.calls==1:self.send_response(503);self.end_headers();return
                b=json.dumps({'protocol':h.PROTOCOL}).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        t=threading.Thread(target=server.serve_forever);t.start()
        try:
            with patch.dict(os.environ,{'HTTP_PROXY':'http://127.0.0.1:1','ALL_PROXY':'http://127.0.0.1:1','NO_PROXY':''}):
                result=h.wait_ready('http://127.0.0.1:'+str(server.server_port)+'/health',lambda b:b['protocol']==h.PROTOCOL,seconds=3)
                self.assertEqual(result['protocol'],h.PROTOCOL);self.assertEqual(Handler.calls,2)
        finally:server.shutdown();server.server_close();t.join()
    def test_timeout_order_and_no_application_retry(self):
        self.assertGreater(h.CLIENT_WORK_TIMEOUT[1],h.UPSTREAM_WORK_TIMEOUT[1])
        self.assertFalse(h.session().trust_env)
        self.assertEqual(h.session().get_adapter('http://localhost').max_retries.total,0)
    def test_child_failure_cancels_readiness(self):
        with self.assertRaises(RuntimeError):h.wait_ready('http://127.0.0.1:1',lambda b:True,cancelled=lambda:True)
if __name__=='__main__':unittest.main()
