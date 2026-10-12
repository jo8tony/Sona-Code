"""Small real stdio peer for protocol/lifecycle tests; no production imports."""
import argparse
import json
import os
import subprocess
import sys
import threading
import time

mode = argparse.ArgumentParser()
mode.add_argument('--mode', default='normal')
mode.add_argument('--no-implementation', action='store_true')
mode.add_argument('--no-references', action='store_true')
args = mode.parse_args()
lock = threading.Lock()
documents = {}
cancellations = 0
client_requests = {}


def send(message):
    data = json.dumps(message, ensure_ascii=False).encode('utf-8')
    frame = f'Content-Length: {len(data)}\r\n\r\n'.encode() + data
    with lock:
        if args.mode == 'split':
            for offset in range(0, len(frame), 3):
                sys.stdout.buffer.write(frame[offset:offset + 3]); sys.stdout.buffer.flush()
        else:
            sys.stdout.buffer.write(frame); sys.stdout.buffer.flush()


def echo(message):
    params = message.get('params', {})
    time.sleep(params.get('delay', 0))
    send({'jsonrpc': '2.0', 'id': message['id'], 'result': params.get('value')})


def definition(message):
    time.sleep(.15)
    root = message['params']['textDocument']['uri'].rsplit('/', 1)[0]
    span = {'start': {'line': 0, 'character': 4}, 'end': {'line': 0, 'character': 10}}
    if message['method'] == 'textDocument/implementation':
        result = [{'targetUri': root + '/implementation.py', 'targetRange': span, 'targetSelectionRange': span},
                  {'uri': root + '/../../outside.py', 'range': span},
                  {'uri': 'jdt://dependency', 'range': span}]
    elif message['method'] == 'textDocument/references':
        if message['params'].get('context') != {'includeDeclaration': False}:
            send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32602, 'message': 'invalid reference context'}})
            return
        result = [{'uri': root + '/target.py', 'range': span},
                  {'uri': root + '/target.py', 'range': span},
                  {'uri': root + '/../../outside.py', 'range': span},
                  {'uri': 'jdt://dependency', 'range': span}]
    else:
        result = [{'uri': root + '/target.py', 'range': span}]
    send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})


while True:
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        if line == b'\r\n':
            break
        name, value = line.decode().split(':', 1); headers[name.lower()] = value.strip()
    message = json.loads(sys.stdin.buffer.read(int(headers['content-length'])))
    method = message.get('method')
    if method == 'initialize':
        initialize_id = message['id']
        send({'jsonrpc': '2.0', 'id': 'config', 'method': 'workspace/configuration', 'params': {'items': [{}]}})
    elif message.get('id') == 'config':
        send({'jsonrpc': '2.0', 'id': initialize_id, 'result': {'capabilities': {'textDocumentSync': 2 if args.mode == 'analysis' else 1, 'definitionProvider': True, 'implementationProvider': not args.no_implementation, 'referencesProvider': not args.no_references}, 'configuration': message.get('result')}})
    elif message.get('id') in client_requests:
        response = {'error': message['error']} if 'error' in message else {'result': message.get('result')}
        send({'jsonrpc': '2.0', 'id': client_requests.pop(message['id']), **response})
    elif method == 'textDocument/didOpen':
        doc = message['params']['textDocument']; documents[doc['uri']] = doc
    elif method == 'textDocument/didChange':
        params = message['params']; doc = documents[params['textDocument']['uri']]
        doc['text'] = params['contentChanges'][0]['text']; doc['version'] = params['textDocument']['version']
    elif method == 'textDocument/didClose':
        documents.pop(message['params']['textDocument']['uri'], None)
    elif method in ('textDocument/definition', 'textDocument/implementation', 'textDocument/references'):
        threading.Thread(target=definition, args=(message,), daemon=True).start()
    elif method == 'test/documents':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': documents})
    elif method == 'test/cancellations':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': cancellations})
    elif method == 'test/spawn-child':
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': child.pid})
    elif method == 'test/stop-reading':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
        time.sleep(30)
    elif method == 'test/pause-reading':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
        time.sleep(4)
    elif method in ('test/client-applyEdit', 'test/client-command'):
        client_id = 'client-' + str(message['id']); client_requests[client_id] = message['id']
        requested = 'workspace/applyEdit' if method.endswith('applyEdit') else 'workspace/executeCommand'
        send({'jsonrpc': '2.0', 'id': client_id, 'method': requested, 'params': {}})
    elif method == 'echo':
        threading.Thread(target=echo, args=(message,), daemon=True).start()
    elif method == 'exit-now':
        os._exit(0)
    elif method == 'bad-id':
        send({'jsonrpc': '2.0', 'id': [], 'result': None})
    elif method == '$/cancelRequest':
        cancellations += 1
        send({'jsonrpc': '2.0', 'method': 'cancelled', 'params': message['params']})
    elif method == 'shutdown' and args.mode != 'hang':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
    elif method == 'exit' and args.mode != 'hang':
        sys.exit(0)
    elif 'id' in message and args.mode != 'hang':
        send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
