"""Android contract probe: durable SSE state, reconnect, final XML verification.

The credential file is token-create JSON or a raw token. Never print credentials.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
from lxml import etree

from capture2doc.pipeline.store import write_json
from capture2doc.service.events import apply_preview_patch


def read_events(response):
    event = {}
    for line in response.iter_lines():
        if not line:
            if 'data' in event:
                yield event.get('event', 'message'), int(event['id']), json.loads(event['data'])
            event = {}
        elif not line.startswith(':'):
            name, _, value = line.partition(':')
            value = value.removeprefix(' ')
            if name == 'data' and name in event:
                event[name] += '\n' + value
            else:
                event[name] = value


def collect(base_url, token, document_id, destination, *, reconnect_once=True, timeout=7200):
    destination.mkdir(parents=True, exist_ok=True)
    state_path = destination / 'preview.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        'documentId': document_id, 'blocks': [], 'revision': 0, 'cursor': None,
        'firstBlockAt': None, 'events': [], 'reconnections': 0}
    if state['documentId'] != document_id:
        raise ValueError('Saved preview belongs to another document')
    started = time.monotonic()
    started_wall = time.time()
    reconnect_done = state['reconnections'] > 0
    with httpx.Client(base_url=base_url, headers={'Authorization': 'Bearer ' + token}, timeout=45) as client:
        terminal = False
        while not terminal:
            if time.monotonic() - started > timeout:
                raise TimeoutError('Document deadline exceeded')
            headers = {} if state['cursor'] is None else {'Last-Event-ID': str(state['cursor'])}
            try:
                connected_at = time.time()
                with client.stream('GET', f'/v1/documents/{document_id}/events', headers=headers) as stream:
                    if stream.status_code == 409:
                        state.update(cursor=None, blocks=[], revision=0)
                        write_json(state_path, state)
                        continue
                    if stream.status_code == 204:
                        terminal = True
                        break
                    stream.raise_for_status()
                    for kind, cursor, data in read_events(stream):
                        received = time.time()
                        if kind != 'document.snapshot' and state['cursor'] is not None and cursor <= state['cursor']:
                            continue
                        if kind == 'document.snapshot':
                            state.update(blocks=data['blocks'], revision=data['revision'])
                            terminal = data['status'] in ('COMPLETED', 'FAILED')
                        elif kind == 'blocks.patch':
                            state['blocks'], state['revision'] = apply_preview_patch(state['blocks'], state['revision'], data)
                        elif kind in ('document.completed', 'document.failed'):
                            terminal = True
                        if state['blocks'] and state['firstBlockAt'] is None:
                            state['firstBlockAt'] = received
                        state['cursor'] = cursor
                        state['events'].append({'id': cursor, 'kind': kind, 'receivedAt': received,
                            'deliveryMs': received * 1000 - data['publishedAtUnixMs'] if 'publishedAtUnixMs' in data else None,
                            'replayed': data.get('publishedAtUnixMs', 0) < connected_at * 1000, 'data': data})
                        # Preview and cursor are one atomic replacement.
                        write_json(state_path, state)
                        if terminal:
                            break
                        if reconnect_once and not reconnect_done and kind == 'blocks.patch':
                            reconnect_done = True
                            state['reconnections'] += 1
                            write_json(state_path, state)
                            break
                if not terminal:
                    time.sleep(.2)
            except (httpx.TransportError, ValueError) as exc:
                state['reconnections'] += 1
                # A revision mismatch must obtain a fresh consistent snapshot.
                if not isinstance(exc, httpx.TransportError):
                    state.update(cursor=None, blocks=[], revision=0)
                write_json(state_path, state)
                time.sleep(1)
        result = client.get(f'/v1/documents/{document_id}')
        result.raise_for_status()
        result = result.json()
        write_json(destination / 'document.json', result)
        if result['status'] != 'COMPLETED':
            raise RuntimeError('Document did not complete: ' + result['status'])
        xml = result['c2dXml']
        if xml is not None:
            raw = xml.encode('utf-8')
            assert hashlib.sha256(raw).hexdigest() == result['sha256'], 'Final digest mismatch'
            (destination / 'document.xml').write_bytes(raw)
            root = etree.fromstring(raw)
            nodes = list(root)
            final_nodes = [etree.tostring(n, method='c14n') for n in nodes]
            preview_nodes = [etree.tostring(etree.fromstring(b['xml'].encode()), method='c14n') for b in state['blocks']]
            assert final_nodes == preview_nodes, 'SSE preview differs from final XML'
        else:
            assert not state['blocks'] and result['sha256'] is None
        # Replay backlog latency is recorded separately from live delivery latency.
        delays = [e['deliveryMs'] for e in state['events'] if e['deliveryMs'] is not None and not e['replayed']]
        summary = {'documentId': document_id, 'status': result['status'], 'blockCount': len(state['blocks']),
            'sha256': result['sha256'], 'elapsedSeconds': time.monotonic() - started,
            'firstBlockSeconds': state['firstBlockAt'] - started_wall if state['firstBlockAt'] else None,
            'maxLiveDeliveryMs': max(delays, default=None), 'reconnections': state['reconnections'],
            'previewMatchesFinal': True}
        write_json(destination / 'summary.json', summary)
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--document-id')
    parser.add_argument('--images', type=Path, nargs='*')
    args = parser.parse_args()
    value = args.token_file.read_text().strip()
    token = json.loads(value)['token'] if value.startswith('{') else value
    if args.document_id:
        summary = collect(args.base_url, token, args.document_id, args.output)
    else:
        if not args.images:
            parser.error('--images or --document-id is required')
        key = 'probe-' + uuid4().hex
        with httpx.Client(base_url=args.base_url, headers={'Authorization': 'Bearer ' + token}, timeout=120) as client:
            response = client.post('/v1/documents', json={'clientTaskId': key}, headers={'Idempotency-Key': key})
            response.raise_for_status()
            identity = response.json()['documentId']
            args.output.mkdir(parents=True, exist_ok=True)
            write_json(args.output / 'request.json', {'documentId': identity, 'clientTaskId': key,
                'images': [str(p) for p in args.images]})
            values, errors = [], []
            def subscribe():
                try:
                    values.append(collect(args.base_url, token, identity, args.output))
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=subscribe, daemon=True)
            thread.start()
            order = []
            for index, path in enumerate(args.images, 1):
                image_id = f'image-{index}'
                raw = path.read_bytes()
                uploaded = client.put(f'/v1/documents/{identity}/pages/{image_id}', content=raw,
                    headers={'Content-Type': 'image/jpeg', 'X-Content-SHA256': hashlib.sha256(raw).hexdigest()})
                uploaded.raise_for_status()
                order.append(image_id)
            response = client.post(f'/v1/documents/{identity}/finalize', json={'pageIds': order},
                headers={'Idempotency-Key': key + '-finalize'})
            response.raise_for_status()
            thread.join(7200)
            if errors:
                raise errors[0]
            if not values:
                raise TimeoutError('Subscriber did not finish')
            summary = values[0]
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
