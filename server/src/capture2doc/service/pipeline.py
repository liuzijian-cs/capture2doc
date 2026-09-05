"""Checkpoint-owned outbox and verified terminal OCR bridge for the service."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from lxml import etree

from capture2doc.pipeline.blocks import document_xml, plain_text, segments
from capture2doc.pipeline.document import BlockStore, json_bytes, read_artifact
from capture2doc.pipeline.store import atomic_write, digest
from .repository import Repository


def visible_blocks(state):
    visible = [b for b in state.get('blocks', []) if b.get('xml')]
    # Committed corruption is fatal. A draft subset may instead be temporarily
    # incompatible with the retained old tail and must wait for related repairs.
    document_xml(visible, state.get('lang'))
    draft = state.get('draft')
    if draft:
        for block in draft['blocks']:
            if block['status'] not in ('ok', 'fallback') or block['final_validation'] != 'passed' or not block['xml']:
                continue
            block = deepcopy(block)
            proposed = list(visible)
            if block.get('replaces_tail'):
                block['id'] = draft['old_tail']['id']
                if proposed and proposed[-1]['id'] == block['id']:
                    proposed.pop()
            proposed.append(block)
            try:
                document_xml(proposed, state.get('lang'))
            except ValueError:
                continue
            visible = proposed
    return [{'blockId': b['id'], 'xml': b['xml']} for b in visible]


class ServiceBlockStore(BlockStore):
    def __init__(self, root: Path, repo: Repository, document_id: str):
        super().__init__(root)
        self.repo, self.document_id = repo, document_id
        self.publishing = False

    def save(self):
        if self.publishing:
            blocks = visible_blocks(self.state)
            status = {'completed':'VALIDATING','ocr':'OCR','assembling':'ASSEMBLING'}.get(self.state.get('status'),'ASSEMBLING')
            order = self.state['ordered_image_ids']
            batches = len(self.state.get('batches',[]))
            payload = {'blocks':blocks,'progress':{
                'status':status,'currentImageId':order[batches] if batches < len(order) else None,
                'completedOcrImages':sum(self.state['images'][i].get('ocr') is not None for i in order),
                'completedImages':batches,'totalImages':len(order)}}
            signature = digest(json_bytes(payload))
            if signature != self.state.get('service_preview_signature'):
                self.state.setdefault('service_outbox',[]).append({'id':uuid4().hex,'payload':payload})
                self.state['service_preview_signature']=signature
        super().save()  # Draft/commit and its outbox are one fsynced checkpoint.
        if self.publishing:
            self.drain()

    def drain(self):
        pending = self.state.get('service_outbox',[])
        for record in pending:
            self.repo.consume(self.document_id,record['id'],record['payload'])
        if pending:
            self.state['service_outbox']=[]
            super().save()  # A crash before this ack only replays receipt IDs.


def seed_terminal_ocr(store: BlockStore, image_id: str, source: BlockStore, configuration: dict, models):
    target = store.state['images'][image_id]
    original = source.state['images'][image_id]
    if source.state['document_id'] != store.state['document_id'] or target['sha256'] != original['sha256']:
        raise ValueError('Service OCR input identity mismatch')
    old = source.state['contract']['model_configuration']
    if old.get('paddle', old) != configuration.get('paddle',configuration):
        raise ValueError('Service OCR model/configuration mismatch')
    prepared = f'prepared/{image_id}.png'
    info = models.image_info(store.root / target['path'],store.root / prepared)
    if info['model_image_sha256'] != original['model_image_sha256']:
        raise ValueError('Service OCR preprocessing mismatch')
    read_artifact(source,original['model_path'],original['model_image_sha256'])
    ocr = deepcopy(original['ocr'])
    if ocr is None:
        raise ValueError('Cannot seed an unfinished OCR task')
    if ocr.get('response_ref'):
        raw = read_artifact(source,ocr['response_ref'],ocr['response_sha256'])
        response = json.loads(raw)
        choice = response['choices'][0]
        if choice['message']['content'] != ocr['content'] or choice['finish_reason'] != ocr['finish_reason']:
            raise ValueError('Service OCR raw response mismatch')
        if ocr['complete'] != (choice['finish_reason']=='stop' and bool(ocr['content'].strip())):
            raise ValueError('Service OCR completeness mismatch')
        atomic_write(store.root / ocr['response_ref'],raw)
    elif ocr.get('error') != 'OCR_RETRIES_EXHAUSTED' or len(original['ocr_attempts']) != 3:
        raise ValueError('Service OCR terminal failure is not accounted for')
    if target.get('ocr') is not None and target['ocr'] != ocr:
        raise ValueError('Service OCR checkpoint changed')
    target.update(info,model_path=prepared,ocr=ocr,ocr_attempts=deepcopy(original['ocr_attempts']),
                  sources=segments(image_id,ocr['content']))
    store.save()


def final_result(store):
    state=store.state
    if state['status']!='completed' or len(state['batches'])!=len(state['ordered_image_ids']):
        raise ValueError('Cannot publish an unfinished document')
    xml = document_xml(state['blocks'],state['lang'])
    title, text = '文档',''
    if xml:
        # Export must already exist with exactly the authoritative bytes.
        candidates=[name for name in state['exports'] if name.endswith('.c2d.xml')]
        if len(candidates)!=1 or read_artifact(store,candidates[0],state['exports'][candidates[0]])!=xml:
            raise ValueError('Final XML export mismatch')
        root=etree.fromstring(xml,etree.XMLParser(resolve_entities=False,no_network=True))
        titles=[n for n in root if etree.QName(n).localname=='title']
        headings=[n for n in root if etree.QName(n).localname in ('h1','h2','h3','h4','h5','h6')]
        title=next((plain_text(n).strip() for n in titles+headings if plain_text(n).strip()),'文档')
        text='\n'.join(plain_text(n) for n in root)
    return {'documentId':state['document_id'],'schemaVersion':'0.1','status':'COMPLETED',
            'title':title,'wordCount':sum(not c.isspace() for c in text),'needsReview':False,
            'c2dXml':xml.decode('utf-8') if xml else None,'sha256':digest(xml) if xml else None}
