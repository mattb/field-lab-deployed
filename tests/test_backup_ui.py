import http.client
import json
import shutil
import subprocess
import threading
import time
import unittest

from ditnotes.backup_ui import PAGE, serve

NODE = shutil.which('node')


class Manager:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0
        self.fail = False

    def status(self):
        return {'message': 'Connect backup drive B002', 'jobs': [{'label': '<img src=x onerror=alert(1)>', 'state': 'pending'}]}

    def run_pending(self):
        self.calls += 1
        self.entered.set()
        self.release.wait(2)
        if self.fail:
            raise RuntimeError('Drive disconnected')


class BackupUITests(unittest.TestCase):
    def setUp(self):
        self.manager = Manager()
        self.server = serve(self.manager)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.manager.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def request(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        result = r.status, dict(r.getheaders()), r.read().decode()
        c.close()
        return result

    def run_headers(self):
        return {'Origin': self.server.origin, 'X-Field-Lab-CSRF': self.server.csrf_token, 'Content-Type': 'application/json'}

    def test_loopback_status_and_safe_rendering(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')
        code, headers, body = self.request('GET', '/')
        self.assertEqual(code, 200)
        self.assertIn('Back up pending footage', body)
        self.assertIn('textContent', body)
        self.assertNotIn('innerHTML', body)
        self.assertNotIn('<img src=x', body)
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        code, _, body = self.request('GET', '/api/status')
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)['message'], 'Connect backup drive B002')
        self.assertFalse(json.loads(body)['ui_running'])

    @unittest.skipUnless(NODE, 'Node is needed to exercise browser text rendering')
    def test_render_has_human_status_and_no_internal_identifiers(self):
        script = PAGE.split('<script>')[1].split('</script>')[0].replace('__TOKEN__', '"test"')
        payload = {'message':'Connect backup drive B002','sets':[{'id':'B001','state':'Closed','bytes':1800000000,'message':'Metadata abcdef1234'}], 'jobs':[{'id':'internal-uuid-do-not-show','label':'20261111/A001','state':'verified','bytes':1400,'message':'B001'},{'id':'another-internal-uuid','label':'20261112/A002','state':'pending','bytes':5000000000,'message':'Awaiting allocation'}]}
        harness = r"""
const vm=require('node:vm');
class Element {constructor(){this.children=[];this.textContent='';this.disabled=false;}appendChild(e){this.children.push(e)}replaceChildren(){this.children=[]}addEventListener(){} }
const elements={message:new Element(),items:new Element(),run:new Element(),refresh:new Element()};
const context={document:{createElement:()=>new Element(),getElementById:id=>elements[id]},fetch:()=>new Promise(()=>{}),setInterval:()=>{}};
vm.createContext(context);vm.runInContext(SCRIPT,context);vm.runInContext('render('+JSON.stringify(PAYLOAD)+')',context);
function content(e){return [e.textContent,...e.children.map(content)].join(' ')}
process.stdout.write(content(elements.items)+' '+elements.message.textContent);
""".replace('SCRIPT',json.dumps(script)).replace('PAYLOAD',json.dumps(payload))
        result = subprocess.run([NODE,'-e',harness],capture_output=True,text=True,check=True,timeout=5).stdout
        self.assertIn('20261111/A001',result)
        self.assertIn('Backed up · B001',result)
        self.assertIn('Awaiting backup',result)
        self.assertIn('Closed · 1.8 GB assigned',result)
        self.assertIn('1.4 KB',result)
        self.assertIn('Notes backed up',result)
        self.assertIn('Connect backup drive B002',result)
        self.assertNotIn('internal-uuid',result)
        self.assertNotIn('abcdef1234',result)
        self.assertNotIn('bytes:',result)
        self.assertNotIn('state:',result)

    def test_host_origin_and_token_fail_closed(self):
        self.assertEqual(self.request('GET', '/api/status', headers={'Host': 'evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/api/run', '{}')[0], 403)
        h = self.run_headers(); h['Origin'] = 'https://evil.example'
        self.assertEqual(self.request('POST', '/api/run', '{}', h)[0], 403)
        h = self.run_headers(); h['X-Field-Lab-CSRF'] = 'bad'
        self.assertEqual(self.request('POST', '/api/run', '{}', h)[0], 403)
        self.assertEqual(self.manager.calls, 0)

    def test_bounded_json_no_path_or_command_parameters(self):
        for body in ('[]', '{"path":"/Volumes/real"}', '{', 'x'*1025):
            self.assertEqual(self.request('POST', '/api/run', body, self.run_headers())[0], 400)
        self.assertEqual(self.manager.calls, 0)

    def test_single_worker_and_live_status_while_running(self):
        self.assertEqual(self.request('POST', '/api/run', '{}', self.run_headers())[0], 202)
        self.assertTrue(self.manager.entered.wait(1))
        self.assertEqual(self.request('POST', '/api/run', '{}', self.run_headers())[0], 409)
        code, _, body = self.request('GET', '/api/status')
        self.assertEqual(code, 200)
        self.assertTrue(json.loads(body)['ui_running'])
        self.assertEqual(self.manager.calls, 1)

    def test_worker_error_is_visible_and_does_not_hold_lock(self):
        self.manager.fail = True
        self.manager.release.set()
        self.assertEqual(self.request('POST', '/api/run', '{}', self.run_headers())[0], 202)
        deadline = time.monotonic()+2
        while self.server.ui_running and time.monotonic()<deadline:
            time.sleep(.005)
        code, _, body = self.request('GET', '/api/status')
        status = json.loads(body)
        self.assertEqual(status['ui_error'], 'Drive disconnected')
        self.assertFalse(status['ui_running'])
        self.assertEqual(self.request('POST', '/api/run', '{}', self.run_headers())[0], 202)

    def test_status_error_returns_unknown_not_success(self):
        def broken():
            raise RuntimeError('Ledger unavailable')
        self.manager.status = broken
        code, _, body = self.request('GET', '/api/status')
        self.assertEqual(code, 503)
        self.assertEqual(json.loads(body)['error'], 'Ledger unavailable')


if __name__ == '__main__':
    unittest.main()
