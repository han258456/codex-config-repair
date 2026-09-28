"""Optional local Codex parser integration check; no model requests or real credentials."""
import argparse
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codex_repair.engine import ConnectionSettings, Options, analyze, execute, RELAY_PROVIDER


def check(codex: str, home: Path, expected: str):
    environment = os.environ.copy()
    environment['CODEX_HOME'] = str(home)
    for key in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'CHATGPT_BASE_URL'):
        environment.pop(key, None)
    lines = queue.Queue()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([codex, 'app-server', '--stdio', '--strict-config'], cwd=home,
                                   env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=errors, text=True, encoding='utf-8')
        def consume():
            for line in process.stdout:
                try:
                    lines.put(json.loads(line))
                except ValueError:
                    pass
            lines.put(None)
        reader = threading.Thread(target=consume, daemon=True)
        reader.start()

        def send(value):
            process.stdin.write(json.dumps(value) + '\n')
            process.stdin.flush()

        def receive(request_id):
            while True:
                try:
                    result = lines.get(timeout=20)
                except queue.Empty:
                    raise RuntimeError('Local Codex parser timed out.') from None
                if result is None:
                    raise RuntimeError('Local Codex parser exited unexpectedly; no private output is displayed.')
                if result.get('id') == request_id:
                    if 'error' in result:
                        raise RuntimeError('Local Codex rejected the synthetic configuration.')
                    return result.get('result', {})
        try:
            send({'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'codex_config_repair_validation', 'version': '1.1.0'}}})
            receive(1)
            send({'method': 'initialized'})
            send({'id': 2, 'method': 'config/read', 'params': {'includeLayers': False}})
            result = receive(2)
            config = result.get('config', {})
            if config.get('model_provider') != expected:
                raise RuntimeError('The parsed provider differs from the requested connection.')
        finally:
            process.stdin.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=5)
            reader.join(timeout=5)
            process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codex', required=True, help='Path to a local Codex executable')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='codex-repair-config-check-') as directory:
        home = Path(directory)
        (home / 'config.toml').write_text('model = "gpt-6-astra"\n', encoding='utf-8')
        options = Options(False, False, False, ConnectionSettings('relay', 'https://gateway.example.com/v1', 'FAKE_PARSER_CHECK_KEY'))
        execute(analyze(home, options=options), process_probe=lambda: [])
        check(args.codex, home, RELAY_PROVIDER)
        options = Options(False, False, False, ConnectionSettings('direct'))
        execute(analyze(home, options=options), process_probe=lambda: [])
        check(args.codex, home, 'openai')
    print(json.dumps({'strict_codex_relay_config': 'passed', 'strict_codex_direct_config': 'passed', 'model_requests': 0, 'fixture_only': True}))


if __name__ == '__main__':
    main()
