from __future__ import annotations

import hashlib
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from capture2doc.formats.c2d_xml import validate_document
from capture2doc.pipeline.blocks import paragraph
from capture2doc.pipeline.document import BlockStore
from capture2doc.service.api import create_app
from capture2doc.service.events import apply_preview_patch, patches
from capture2doc.service.pipeline import ServiceBlockStore, visible_blocks
from capture2doc.service.repository import Repository, ServiceError
from capture2doc.service.settings import Settings
from capture2doc.service.worker import Worker
from test_block_pipeline import Models, block, submit, repeat_bad
from test_document_pipeline import response


def jpeg(size=(40,30)):
    out=io.BytesIO()
    Image.new('RGB',size,'white').save(out,format='JPEG')
    return out.getvalue()


@pytest.fixture
def service(tmp_path):
    settings=Settings(data_root=tmp_path/'data',gpu_lock=tmp_path/'gpu.lock',min_free_bytes=1,idle_seconds=.01)
    app=create_app(settings)
    repo=app.state.repository
    identity,token=repo.create_token('phone')
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer '+token
        yield settings,repo,client,identity


def create(client,key='task'):
    response=client.post('/v1/documents',json={'clientTaskId':key},headers={'Idempotency-Key':key})
    assert response.status_code in (200,201),response.text
    return response.json()['documentId']


def upload(client,doc,image='a',data=None,sha=None):
    data=data or jpeg()
    return client.put(f'/v1/documents/{doc}/pages/{image}',content=data,
        headers={'Content-Type':'image/jpeg','X-Content-SHA256':sha or hashlib.sha256(data).hexdigest()})


def finalize(client,doc,order,key='task'):
    response=client.post(f'/v1/documents/{doc}/finalize',json={'pageIds':order},headers={'Idempotency-Key':key+'-finalize'})
    assert response.status_code==202,response.text


def drive(settings,repo,models):
    worker=Worker(settings,models)
    while repo.next_work():
        worker.run_available(wait_idle=False)
    return worker


def replay(repo,doc):
    blocks,revision=[],0
    for seq,kind,data in repo.events(doc,0,10000):
        if kind=='blocks.patch':
            blocks,revision=apply_preview_patch(blocks,revision,data)
    _,snapshot=repo.snapshot(doc)
    assert (blocks,revision)==(snapshot['blocks'],snapshot['revision'])
    return blocks


def test_upload_identity_finalize_and_auth(service):
    settings,repo,client,identity=service
    doc=create(client)
    assert create(client)==doc
    assert upload(client,doc,'a').status_code==201
    assert upload(client,doc,'a').status_code==200
    assert upload(client,doc,'a',jpeg((41,31))).status_code==409
    assert upload(client,doc,'b',sha='0'*64).status_code==400
    assert not list((repo.root/'tmp').glob('*.upload'))
    assert upload(client,doc,'b',b'not a jpeg').status_code==400
    assert upload(client,doc,'b',jpeg((1281,1))).status_code==413
    assert upload(client,doc,'b').status_code==201
    finalize(client,doc,['b','a'])
    finalize(client,doc,['b','a'])
    assert client.post(f'/v1/documents/{doc}/finalize',json={'pageIds':['a','b']},headers={'Idempotency-Key':'task-finalize'}).status_code==409
    assert upload(client,doc,'c').status_code==409
    assert upload(client,doc,'a').status_code==200
    repo.revoke(identity)
    assert client.get(f'/v1/documents/{doc}').status_code==401
    assert client.get(f'/v1/documents/{doc}/events').status_code==401


def test_reservations_include_inflight_and_cleanup(service):
    settings,repo,client,_=service
    doc=create(client)
    small=Repository(replace(settings,max_images=1))
    reserved,_=small.reserve_upload(doc,'a','a'*64,100)
    with pytest.raises(ServiceError) as exc:
        small.reserve_upload(doc,'b','b'*64,100)
    assert exc.value.status==413
    with pytest.raises(ServiceError) as exc:
        small.reserve_upload(doc,'a','a'*64,100)
    assert exc.value.status==429
    small.cancel_upload(reserved)
    limited=Repository(replace(settings,max_data_bytes=repo.used_bytes()+150))
    reserve,_=limited.reserve_upload(doc,'a','a'*64,100)
    with pytest.raises(ServiceError) as exc:
        limited.reserve_upload(doc,'b','b'*64,100)
    assert exc.value.status==507
    limited.cancel_upload(reserve)


def test_early_ocr_final_order_complete_and_event_replay(service):
    settings,repo,client,_=service
    doc=create(client)
    for i in ('a','b','c'): assert upload(client,doc,i).status_code==201
    models=Models({'a':'甲文','b':'排除文字','c':'丙文'})
    worker=Worker(settings,models)
    worker.run_available(wait_idle=False)
    assert len([e for e in models.events if e.startswith('ocr:')])==3
    assert not models.requests
    finalize(client,doc,['c','a'])
    worker.run_available(wait_idle=False)
    result=client.get(f'/v1/documents/{doc}').json()
    assert result['status']=='COMPLETED' and result['needsReview'] is False
    assert validate_document(result['c2dXml']).valid
    assert result['sha256']==hashlib.sha256(result['c2dXml'].encode()).hexdigest()
    assert result['wordCount']==4 and '排除文字' not in result['c2dXml']
    blocks=replay(repo,doc)
    assert '丙文' in blocks[0]['xml'] and '甲文' in blocks[1]['xml']
    sse=client.get(f'/v1/documents/{doc}/events')
    assert sse.headers['content-type'].startswith('text/event-stream')
    assert 'event: document.snapshot' in sse.text
    replayed=client.get(f'/v1/documents/{doc}/events',headers={'Last-Event-ID':'0'})
    assert 'event: blocks.patch' in replayed.text and 'event: document.completed' in replayed.text
    last=repo.snapshot(doc)[0]
    assert client.get(f'/v1/documents/{doc}/events',headers={'Last-Event-ID':str(last)}).status_code==204
    assert client.get(f'/v1/documents/{doc}/events',headers={'Last-Event-ID':str(last+1)}).status_code==409
    assert client.get('/openapi.json').status_code==200
    assert client.get('/docs').status_code==404
    assert not worker.run_available(wait_idle=False)


def test_good_blocks_visible_before_repair_and_middle_insertion(service):
    settings,repo,client,_=service
    doc=create(client)
    assert upload(client,doc).status_code==201
    finalize(client,doc,['a'])
    def repair(payload):
        _,snapshot=repo.snapshot(doc)
        assert len(snapshot['blocks'])==2
        assert '甲' in snapshot['blocks'][0]['xml'] and '丙' in snapshot['blocks'][1]['xml']
        assert repo.public(doc)['status']=='ASSEMBLING'
        return {'action':'submit','attempt_id':payload['attempt_id'],'target_versions':payload['target_versions'],
                'blocks':[block('乙')]}
    models=Models({'a':'甲\n乙\n丙'},actions=[submit(block('甲'),block('乙','<bad/>'),block('丙')),repair])
    drive(settings,repo,models)
    values=replay(repo,doc)
    assert len(values)==3 and all(text in b['xml'] for text,b in zip('甲乙丙',values))
    patches_=[e[2] for e in repo.events(doc,0,100) if e[1]=='blocks.patch']
    assert len(patches_)==3
    assert patches_[-1]['afterBlockId']==values[0]['blockId']


@pytest.mark.parametrize('reason,text',[('length','部分文字'),('stop','')])
def test_incomplete_ocr_and_missing_content_still_complete(service,reason,text):
    settings,repo,client,_=service
    doc=create(client)
    assert upload(client,doc).status_code==201
    finalize(client,doc,['a'])
    class Incomplete(Models):
        def ocr(self,path):
            return response(text,reason)
    models=Incomplete({'a':text},actions=[response('',reason='length')])
    drive(settings,repo,models)
    result=repo.public(doc)
    assert result['status']=='COMPLETED' and result['needsReview'] is False
    if text:
        assert text in result['c2dXml']
    else:
        assert result['c2dXml'] is None and result['sha256'] is None and result['wordCount']==0
    source=BlockStore(repo.document_root(doc)/'ocr/a');source.load()
    target=BlockStore(repo.document_root(doc)/'pipeline');target.load()
    assert source.state['images']['a']['ocr']['complete'] is False
    assert target.state['images']['a']['ocr']['complete'] is False


def test_repair_budget_exhaustion_continues_next_image(service):
    settings,repo,client,_=service
    doc=create(client)
    for i in ('a','b'): assert upload(client,doc,i).status_code==201
    finalize(client,doc,['a','b'])
    models=Models({'a':'首图','b':'后图'},actions=[submit(block('首图','<bad/>'))]+[repeat_bad]*5)
    drive(settings,repo,models)
    result=repo.public(doc)
    assert result['status']=='COMPLETED' and '后图' in result['c2dXml']
    store=BlockStore(repo.document_root(doc)/'pipeline');store.load()
    assert store.state['blocks'][0]['repair_attempts']==5
    assert store.state['blocks'][0]['status']=='fallback'
    assert len(models.requests)==7


def test_patch_splice_and_versions():
    def b(i,v=1):return {'blockId':i,'version':v,'xml':paragraph(i)}
    scenarios=[([], [b('a'),b('c')]),([b('a'),b('c')],[b('a'),b('b'),b('c')]),
        ([b('a'),b('b'),b('c')],[b('a'),b('x'),b('y'),b('c')]),
        ([b('a'),b('b')],[b('a')]),([b('a')],[]),([b('a')],[b('a',2)])]
    for old,new in scenarios:
        current,rev=old,0
        for patch in patches(old,new,rev):
            current,rev=apply_preview_patch(current,rev,patch)
        assert current==new


def test_outbox_replay_after_database_commit_before_ack(service):
    settings,repo,client,_=service
    doc=create(client)
    assert upload(client,doc).status_code==201
    finalize(client,doc,['a'])
    drive(settings,repo,Models({'a':'甲'}))
    # Use a fresh service document and a real checkpoint to exercise interrupted ack.
    target=create(client,'second')
    assert upload(client,target).status_code==201
    finalize(client,target,['a'],key='second')
    source=BlockStore(repo.document_root(doc)/'pipeline');source.load()
    observed=ServiceBlockStore(repo.document_root(target)/'pipeline',repo,target)
    import copy
    observed.state=copy.deepcopy(source.state)
    observed.state['document_id']=target
    observed.state['service_outbox']=[]
    observed.state.pop('service_preview_signature',None)
    observed.publishing=True
    original=repo.consume
    def interrupted(*args):
        original(*args)
        raise KeyboardInterrupt()
    repo.consume=interrupted
    with pytest.raises(KeyboardInterrupt):observed.save()
    saved=json.loads(observed.state_path.read_text())
    assert saved['service_outbox']
    before=repo.events(target,0,100)
    repo.consume=original
    resumed=ServiceBlockStore(observed.root,repo,target)
    resumed.state=saved
    resumed.drain()
    assert repo.events(target,0,100)==before
    assert not json.loads(resumed.state_path.read_text())['service_outbox']


def test_restored_tail_has_same_id_higher_stream_version(service):
    _,repo,client,_=service
    doc=create(client)
    initial=[{'blockId':'tail','xml':paragraph('旧文')}]
    changed=[{'blockId':'tail','xml':paragraph('旧文新文')}]
    for i,values in enumerate((initial,changed,initial,[],initial)):
        repo.consume(doc,str(i),{'blocks':values})
    blocks=replay(repo,doc)
    assert blocks[0]['blockId']=='tail' and blocks[0]['version']==4
    events=repo.events(doc,0,100)
    assert len(events)==5


def test_cleanup_failure_blocks_next_model(service):
    settings,repo,client,_=service
    doc=create(client);assert upload(client,doc).status_code==201
    finalize(client,doc,['a'])
    models=Models({'a':'正文'});models.release_failure=True
    with pytest.raises(RuntimeError):Worker(settings,models).run_available(wait_idle=False)
    assert not models.requests
    assert repo.public(doc)['status']=='FAILED'


def test_config_errors(tmp_path,monkeypatch):
    path=tmp_path/'config.toml';path.write_text('[service]\nport=12345\n')
    monkeypatch.setenv('C2D_PORT','11209')
    assert Settings.load(path).port==11209
    with pytest.raises(ValueError):Settings(poll_seconds=1)
    with pytest.raises(ValueError):Settings(max_upload_bytes=-1)
    path.write_text('[service]\nunknown="bad"\n')
    with pytest.raises(ValueError):Settings.load(path)


def test_tail_preview_id_survives_formal_commit(service):
    settings,repo,client,_=service
    doc=create(client)
    for identity in ('a','b'):assert upload(client,doc,identity).status_code==201
    finalize(client,doc,['a','b'])
    models=Models({'a':'旧尾','b':'新增'},actions=[submit(block('旧尾')),submit(tail=block('旧尾新增'))])
    drive(settings,repo,models)
    changes=[e[2] for e in repo.events(doc,0,1000) if e[1]=='blocks.patch']
    assert len(changes)==2
    first=changes[0]['blocks'][0];last=changes[1]['blocks'][0]
    assert first['blockId']==last['blockId'] and (first['version'],last['version'])==(1,2)
    assert len(replay(repo,doc))==1


def test_snapshot_highwater_has_no_event_gap(service):
    _,repo,client,_=service
    doc=create(client)
    repo.consume(doc,'first',{'blocks':[{'blockId':'a','xml':paragraph('甲')}]})
    cursor,snapshot=repo.snapshot(doc)
    repo.consume(doc,'second',{'blocks':[{'blockId':'a','xml':paragraph('甲')},{'blockId':'b','xml':paragraph('乙')}]})
    values,revision=snapshot['blocks'],snapshot['revision']
    for _,kind,data in repo.events(doc,cursor):
        if kind=='blocks.patch':values,revision=apply_preview_patch(values,revision,data)
    assert values==repo.snapshot(doc)[1]['blocks'] and len(values)==2


def test_event_sql_transaction_rolls_back_projection_and_replays(service):
    _,repo,client,_=service
    doc=create(client)
    payload={'blocks':[{'blockId':'a','xml':paragraph('甲')}]}
    original=repo._event
    def fail(db,*args):
        original(db,*args)
        raise OSError('simulated interruption before SQLite commit')
    repo._event=fail
    with pytest.raises(OSError):repo.consume(doc,'outbox-id',payload)
    assert repo.snapshot(doc)[0]==0 and not repo.snapshot(doc)[1]['blocks']
    repo._event=original
    repo.consume(doc,'outbox-id',payload);repo.consume(doc,'outbox-id',payload)
    assert len(repo.events(doc,0))==1


def test_unexpected_api_failure_is_safe_json(service,monkeypatch):
    _,repo,client,_=service
    doc=create(client)
    def fail(identity):raise RuntimeError('private storage details')
    monkeypatch.setattr(repo,'public',fail)
    public=TestClient(client.app,raise_server_exceptions=False)
    result=public.get(f'/v1/documents/{doc}',headers={'Authorization':client.headers['Authorization']})
    assert result.status_code==500 and set(result.json())=={'message'}
    assert 'private storage' not in result.text


def test_incompatible_draft_subset_waits_without_failing_document():
    from capture2doc.pipeline.blocks import candidate
    from capture2doc.pipeline.draft import initialize,new_draft
    old=candidate(block('原标题','<title>原标题</title>'),'a')
    draft=new_draft('b',old)
    initialize(draft,submit(block('替代标题','<title>替代标题</title>'),tail=block('原标题','<bad/>')))
    values=visible_blocks({'blocks':[old],'draft':draft,'lang':'zh-CN'})
    assert len(values)==1 and '原标题' in values[0]['xml']
