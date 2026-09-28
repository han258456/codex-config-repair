import json
import tempfile
import unittest
from pathlib import Path

import tomlkit

from codex_repair.demo import create_demo, FORK, PARENT
from codex_repair.engine import (
    ConnectionSettings, Options, RELAY_PROVIDER, RepairError, analyze,
    connection_summary, execute, list_backups, restore, readonly_db,
)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.paths = create_demo(self.home)
        self.auth = self.home / 'auth.json'
        self.auth.write_text('{"fake_login":"PRESERVED_LOCAL_LOGIN"}', encoding='utf-8')
        self.config = self.home / 'config.toml'

    def tearDown(self):
        self.temp.cleanup()

    def plan(self, mode='relay', url='https://gateway.example.com/v1', key='FAKE_RELAY_KEY', model='', history=True):
        return analyze(self.home, options=Options(False, False, history, ConnectionSettings(mode, url, key, model)))

    def apply(self, plan):
        return execute(plan, process_probe=lambda: [])

    def parsed(self):
        return tomlkit.parse(self.config.read_text(encoding='utf-8'))

    def test_relay_and_direct_restore_without_touching_auth_or_body(self):
        original = {p: p.read_bytes() for p in [self.config, self.auth, *self.paths.values()]}
        relay_backup = self.apply(self.plan())
        relay = self.parsed()
        self.assertEqual(relay['model_provider'], RELAY_PROVIDER)
        provider = relay['model_providers'][RELAY_PROVIDER]
        self.assertFalse(provider['requires_openai_auth'])
        self.assertEqual(provider['experimental_bearer_token'], 'FAKE_RELAY_KEY')
        self.assertEqual(provider['wire_api'], 'responses')
        with readonly_db(self.home / 'state_5.sqlite') as db:
            self.assertEqual(db.execute('SELECT DISTINCT model_provider FROM threads').fetchall(), [(RELAY_PROVIDER,)])
        parent_size = self.paths[PARENT].stat().st_size
        fork_header = json.loads(self.paths[FORK].read_bytes().split(b'\n', 1)[0])
        self.assertEqual(fork_header['payload']['history_base']['end_byte_offset'], parent_size)
        relay_bytes = self.config.read_bytes()
        direct_backup = self.apply(self.plan(mode='direct'))
        self.assertEqual([item['path'] for item in list_backups(self.home)], [direct_backup, relay_backup])
        self.assertEqual(self.parsed()['model_provider'], 'openai')
        self.assertTrue(connection_summary(self.home)['has_key'])
        for path in self.paths.values():
            self.assertEqual(path.read_bytes().split(b'\n', 1)[1], original[path].split(b'\n', 1)[1])
        self.assertEqual(self.auth.read_bytes(), original[self.auth])
        restore(self.home, direct_backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), relay_bytes)
        restore(self.home, relay_backup, process_probe=lambda: [])
        for path, data in original.items():
            self.assertEqual(path.read_bytes(), data)

    def test_preview_readonly_model_change_and_secret_redaction(self):
        before = self.config.read_bytes()
        secret = 'FAKE_QUOTED_"_\\_KEY'
        plan = self.plan(key=secret, model='vendor-model')
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(plan.model, 'vendor-model')
        self.assertNotIn(secret, repr(plan.options))
        self.assertNotIn('FAKE_QUOTED', json.dumps(plan.report()))
        self.apply(plan)
        self.assertEqual(self.parsed()['model_providers'][RELAY_PROVIDER]['experimental_bearer_token'], secret)
        self.assertNotIn(secret, repr(connection_summary(self.home)))
        self.assertEqual(self.parsed()['model'], 'vendor-model')

    def test_saved_key_reused_only_for_same_endpoint_and_direct_keeps_it(self):
        self.apply(self.plan())
        self.assertFalse(self.plan(key='').needed)
        self.assertFalse(self.plan(url='https://gateway.example.com/v1/', key='').needed)
        with self.assertRaisesRegex(RepairError, '更换接口地址'):
            self.plan(url='https://another.example.com/v1', key='')
        self.apply(self.plan(mode='direct'))
        self.apply(self.plan(key=''))
        self.assertEqual(self.parsed()['model_provider'], RELAY_PROVIDER)

    def test_replacing_url_and_key_works_and_restores(self):
        self.apply(self.plan())
        before = self.config.read_bytes()
        backup = self.apply(self.plan(url='http://localhost:3000/api/v1/', key='NEW_FAKE_KEY'))
        saved = self.parsed()['model_providers'][RELAY_PROVIDER]
        self.assertEqual(saved['base_url'], 'http://localhost:3000/api/v1')
        self.assertEqual(saved['experimental_bearer_token'], 'NEW_FAKE_KEY')
        restore(self.home, backup, process_probe=lambda: [])
        self.assertEqual(self.config.read_bytes(), before)

    def test_direct_removes_endpoint_overrides_but_preserves_login_preferences(self):
        config = self.parsed()
        config['openai_base_url'] = 'https://old.example.com/v1'
        config['chatgpt_base_url'] = 'https://old.example.com/backend'
        config['model_providers'] = {'openai': {'name': 'old', 'base_url': 'https://old.example.com'}}
        config['cli_auth_credentials_store'] = 'keyring'
        self.config.write_text(tomlkit.dumps(config), encoding='utf-8')
        self.assertTrue(connection_summary(self.home)['has_overrides'])
        self.apply(self.plan(mode='direct'))
        saved = self.parsed()
        self.assertNotIn('openai_base_url', saved)
        self.assertNotIn('chatgpt_base_url', saved)
        self.assertNotIn('openai', saved.get('model_providers', {}))
        self.assertEqual(saved['cli_auth_credentials_store'], 'keyring')
        self.assertEqual(saved['notify'], ['CURRENT_RUNTIME'])

    def test_missing_key_and_invalid_inputs_do_not_write(self):
        original = self.config.read_bytes()
        for url in ['gateway.example.com', 'file:///tmp/data', 'https://a.test:bad/v1',
                    'https://user:KEY@a.test/v1', 'https://@a.test/v1', 'https://a.test/v1?key=SECRET',
                    'https://a.test/v1#frag', 'https://a.test/invalid path',
                    'https://a.test/v1/responses', 'https://a.test/v1/chat/completions']:
            with self.subTest(url=url), self.assertRaises(RepairError):
                self.plan(url=url)
        for key in ['', 'a\nb', 'a\tb', '密钥']:
            with self.subTest(key=key), self.assertRaises(RepairError):
                self.plan(key=key)
        with self.assertRaises(RepairError):
            self.plan(mode='unknown')
        with self.assertRaises(RepairError):
            self.plan(model='has spaces')
        self.assertEqual(self.config.read_bytes(), original)
        self.assertFalse((self.home / 'repair-backups').exists())

    def test_replace_provider_clears_stale_auth_overrides(self):
        self.apply(self.plan())
        config = self.parsed()
        relay = config['model_providers'][RELAY_PROVIDER]
        relay['env_key'] = 'STALE_ENV_KEY'
        relay['http_headers'] = {'Authorization': 'FAKE_STALE_HEADER'}
        relay['auth'] = {'command': 'old-command'}
        self.config.write_text(tomlkit.dumps(config), encoding='utf-8')
        self.apply(self.plan())
        relay = self.parsed()['model_providers'][RELAY_PROVIDER]
        for key in ['env_key', 'http_headers', 'auth']:
            self.assertNotIn(key, relay)

    def test_config_only_switch_handles_unknown_history_and_roundtrips(self):
        (self.home / 'thread_history_1.sqlite').unlink()
        files = {path: path.read_bytes() for path in self.paths.values()}
        plan = self.plan(history=False)
        self.assertEqual(len(plan.changes), 1)
        self.apply(plan)
        for path, data in files.items():
            self.assertEqual(path.read_bytes(), data)

    def test_failed_switch_rolls_back_and_reports_no_key(self):
        original = self.config.read_bytes()
        def fail(_):
            raise OSError('synthetic write failure')
        with self.assertRaises(RepairError):
            execute(self.plan(), process_probe=lambda: [], fault_hook=fail)
        self.assertEqual(self.config.read_bytes(), original)

    def test_new_key_only_change_requires_apply_even_when_redacted_diff_empty(self):
        self.apply(self.plan())
        plan = self.plan(key='UPDATED_FAKE_KEY')
        self.assertTrue(plan.needed)
        self.assertEqual(plan.config_diff, '')
        self.apply(plan)
        self.assertEqual(self.parsed()['model_providers'][RELAY_PROVIDER]['experimental_bearer_token'], 'UPDATED_FAKE_KEY')


if __name__ == '__main__':
    unittest.main()
