"""Small real stdio peer for protocol/lifecycle tests; no production imports."""
import argparse
import json
import os
import sys
import threading
import time

mode = argparse.ArgumentParser()
mode.add_argument('--mode', default='normal')
args = mode.parse_args()
lock = threading.Lock()


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
        send({'jsonrpc': '2.0', 'id': initialize_id, 'result': {'capabilities': {'textDocumentSync': 1}, 'configuration': message.get('result')}})
    elif method == 'echo':
        threading.Thread(target=echo, args=(message,), daemon=True).start()
    elif method == 'exit-now':
        os._exit(0)
    elif method == '$/cancelRequest':
        send({'jsonrpc': '2.0', 'method': 'cancelled', 'params': message['params']})
    elif method == 'shutdown' and args.mode != 'hang':
        send({'jsonrpc': '2.0', 'id': message['id'], 'result': None})
    elif method == 'exit' and args.mode != 'hang':
        sys.exit(0)
    elif 'id' in message and args.mode != 'hang':
        send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'unsupported'}})
