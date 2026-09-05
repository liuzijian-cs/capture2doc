"""Real TCP/HTTP tests with genuine persistence and SSE transport."""
import json
import socket
import threading
import time
import httpx
import pytest
import uvicorn
from capture2doc.pipeline.blocks import paragraph
from capture2doc.pipeline.store import exclusive_lock
from capture2doc.service.api import create_app
from capture2doc.service.settings import Settings
from capture2doc.service.worker import Worker
from test_service import create, upload, finalize, replay
from test_block_pipeline import Models, block, submit, repeat_bad

@pytest.fixture
def network(tmp_path):
    settings=Settings(data_root=tmp_path/'data',gpu_lock=tmp_path/'gpu.lock',min_free_bytes=1,
                      heartbeat_seconds=.1,send_timeout_seconds=.3,poll_seconds=.02)
    app=create_app(settings);repo=app.state.repository
    identity,token=repo.create_token('phone')
    sock=socket.socket();sock.bind(('127.0.0.1',0))
    address='http://127.0.0.1:'+str(sock.getsockname()[1])
    server=uvicorn.Server(uvicorn.Config(app,log_level='error'))
    thread=threading.Thread(target=server.run,kwargs={'sockets':[sock]},daemon=True);thread.start()
    deadline=time.monotonic()+5
    while not server.started:
        assert time.monotonic()<deadline
        time.sleep(.01)
    with httpx.Client(base_url=address,headers={'Authorization':'Bearer '+token},timeout=3) as client:
        yield settings,repo,client,identity,app
    server.should_exit=True;thread.join(5);assert not thread.is_alive()

def next_event(lines):
    values={}
    for line in lines:
        if line.startswith(':'):continue
        if not line:
            if 'data' in values:return values['event'],int(values['id']),json.loads(values['data'])
            continue
        key,_,value=line.partition(': ');values[key]=value
    raise EOFError()

def test_live_early_delivery_disconnect_replay_and_revoke(network):
    _,repo,client,identity,app=network
    doc=create(client);endpoint=f'/v1/documents/{doc}/events'
    with client.stream('GET',endpoint) as stream:
        assert stream.status_code==200 and stream.headers['x-accel-buffering']=='no'
        lines=stream.iter_lines();kind,cursor,snapshot=next_event(lines)
        assert kind=='document.snapshot' and not snapshot['blocks']
        started=time.monotonic()
        repo.consume(doc,'one',{'blocks':[{'blockId':'a','xml':paragraph('甲')}]})
        kind,cursor,_=next_event(lines)
        assert kind=='blocks.patch' and time.monotonic()-started<1
        with client.stream('GET',endpoint) as second:
            assert second.status_code==200
            assert client.get(endpoint).status_code==429
            next_event(second.iter_lines())
        _,other=repo.create_token('tablet')
        with client.stream('GET',endpoint,headers={'Authorization':'Bearer '+other}) as third:
            assert third.status_code==200
            next_event(third.iter_lines())
    deadline=time.monotonic()+3
    while app.state.stream_connections:
        assert time.monotonic()<deadline
        time.sleep(.02)
    repo.consume(doc,'two',{'blocks':[{'blockId':'a','xml':paragraph('甲')},{'blockId':'b','xml':paragraph('乙')}]})
    with client.stream('GET',endpoint,headers={'Last-Event-ID':str(cursor)}) as stream:
        lines=stream.iter_lines();kind,new_cursor,_=next_event(lines)
        assert kind=='blocks.patch' and new_cursor>cursor
        repo.revoke(identity)
        with pytest.raises(EOFError):next_event(lines)
    assert client.get(f'/v1/documents/{doc}').status_code==401

def test_worker_resume_preserves_repair_budget_and_gpu_lock(network):
    settings,repo,client,_,_=network
    doc=create(client);assert upload(client,doc).status_code==201;finalize(client,doc,['a'])
    models=Models({'a':'正文'})
    with exclusive_lock(settings.gpu_lock):
        assert Worker(settings,models).run_available(wait_idle=False) is False
        assert repo.public(doc)['status']!='FAILED'
    def interrupt(payload):raise KeyboardInterrupt()
    models=Models({'a':'正文'},actions=[submit(block('正文','<bad/>')),repeat_bad,interrupt])
    with pytest.raises(KeyboardInterrupt):Worker(settings,models).run_available(wait_idle=False)
    assert repo.public(doc)['status']!='FAILED'
    from capture2doc.pipeline.document import BlockStore
    store=BlockStore(repo.document_root(doc)/'pipeline');store.load()
    resumed=Models({'a':'正文'},actions=[repeat_bad]*5)
    Worker(settings,resumed).run_available(wait_idle=False);store.load()
    assert store.state['blocks'][0]['repair_attempts']==5
    assert repo.public(doc)['status']=='COMPLETED'
    assert not any(e.startswith('ocr:') for e in resumed.events)
    assert len(resumed.requests)<5
    replay(repo,doc)


def test_probe_final_verification_and_lost_snapshot(network,tmp_path):
    settings,repo,client,_,_=network
    doc=create(client);assert upload(client,doc).status_code==201;finalize(client,doc,['a'])
    Worker(settings,Models({'a':'完整正文'})).run_available(wait_idle=False)
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('probe',Path(__file__).parents[1]/'scripts/test_service_client.py')
    probe=importlib.util.module_from_spec(spec);spec.loader.exec_module(probe)
    token=client.headers['Authorization'].removeprefix('Bearer ')
    result=probe.collect(str(client.base_url),token,doc,tmp_path/'output')
    assert result['previewMatchesFinal'] and result['blockCount']==1
    # Losing a snapshot requires a new consistent snapshot, not cursor-only replay.
    (tmp_path/'output/preview.json').unlink()
    assert probe.collect(str(client.base_url),token,doc,tmp_path/'output')['sha256']==result['sha256']


def test_aborted_upload_releases_reservation(network):
    _,repo,client,_,_=network
    doc=create(client)
    host,port=client.base_url.host,client.base_url.port
    raw=socket.create_connection((host,port))
    request=(f'PUT /v1/documents/{doc}/pages/cut HTTP/1.1\r\nHost: {host}\r\n'
             f'Authorization: {client.headers["Authorization"]}\r\nContent-Type: image/jpeg\r\n'
             f'X-Content-SHA256: {"0"*64}\r\nContent-Length: 10000\r\n\r\n').encode()
    raw.sendall(request+b'partial')
    deadline=time.monotonic()+3
    while True:
        with repo.connection() as db:count=db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]
        if count:break
        assert time.monotonic()<deadline;time.sleep(.01)
    raw.close()
    deadline=time.monotonic()+3
    while True:
        with repo.connection() as db:count=db.execute('SELECT COUNT(*) FROM uploads').fetchone()[0]
        if not count:break
        assert time.monotonic()<deadline;time.sleep(.01)
    assert not list((repo.root/'tmp').glob('*.upload'))
    assert not repo.images(doc)


def test_slow_sender_timeout_does_not_hold_worker():
    import anyio
    from sse_starlette import EventSourceResponse, ServerSentEvent
    from sse_starlette.sse import SendTimeoutError
    async def scenario():
        released=anyio.Event()
        async def body():
            try:
                yield ServerSentEvent(data='complete block')
            finally:
                released.set()
        response=EventSourceResponse(body(),send_timeout=.03,ping=15)
        async def send(message):
            if message['type']=='http.response.body':await anyio.sleep(10)
        async def receive():
            await anyio.sleep(10)
            return {'type':'http.disconnect'}
        started=time.monotonic()
        try:
            await response({'type':'http','asgi':{'version':'3.0'},'method':'GET','path':'/events','headers':[]},receive,send)
        except SendTimeoutError:
            pass
        assert released.is_set() and time.monotonic()-started<1
    anyio.run(scenario)
