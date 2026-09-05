"""One durable GPU scheduler, independent from HTTP and SSE connections."""
from __future__ import annotations

import json
import logging
import signal
import threading
import time
from contextlib import ExitStack
from uuid import uuid4

from capture2doc.pipeline.document import BlockStore, run_document_v2, run_ocr
from capture2doc.pipeline.models import LocalModels, verify_previous_cleanup
from capture2doc.pipeline.store import digest, exclusive_lock, write_json
from .pipeline import ServiceBlockStore, final_result, seed_terminal_ocr
from .repository import Repository
from .settings import Settings

log=logging.getLogger(__name__)


class Worker:
    def __init__(self, settings: Settings, models=None):
        self.settings=settings
        self.repo=Repository(settings)
        self.models=models or LocalModels(cache_dir=str(settings.model_cache),host=settings.model_host)
        self.configuration=None

    def prepare(self):
        if self.configuration is None:
            self.configuration=self.models.prepare()
        return self.configuration

    def ocr_store(self, doc, image):
        root=self.repo.document_root(doc['id']) / 'ocr' / image['id']
        path=self.repo.root / image['path']
        if digest(path.read_bytes())!=image['sha256']:
            raise ValueError('Uploaded image identity changed')
        store=BlockStore(root)
        if store.state_path.exists():
            store.load()
        else:
            manifest=root / 'input.json'
            write_json(manifest,{'document_id':doc['id'],'lang':'zh-CN',
                'images':[{'image_id':image['id'],'path':str(path)}],'ordered_image_ids':[image['id']]})
            store.create(manifest)
        store.bind_contract({'service_ocr_version':1,'model_configuration':self.prepare()})
        item=store.state['images'][image['id']]
        prepared=f"prepared/{image['id']}.png"
        info=self.models.image_info(store.root/item['path'],store.root/prepared)
        if item.get('model_image_sha256') not in (None,info['model_image_sha256']):
            raise ValueError('OCR image preprocessing changed')
        item.update(info,model_path=prepared)
        store.save()
        return store

    def assemble(self, doc):
        document_id=doc['id']
        root=self.repo.document_root(document_id)/'pipeline'
        store=ServiceBlockStore(root,self.repo,document_id)
        with exclusive_lock(root/'.document.lock'):
            if store.state_path.exists():
                store.load()
                store.drain()
            else:
                images={r['id']:r for r in self.repo.images(document_id)}
                order=json.loads(doc['final_order'])
                manifest=root/'input.json'
                write_json(manifest,{'document_id':document_id,'lang':'zh-CN','ordered_image_ids':order,
                    'images':[{'image_id':i,'path':str(self.repo.root/images[i]['path'])} for i in order]})
                store.create(manifest)
            # A completed checkpoint only rebuilds verified exports; no GPU startup.
            if store.state['status']!='completed':
                configuration=self.prepare()
                for identity in store.state['ordered_image_ids']:
                    source=BlockStore(self.repo.document_root(document_id)/'ocr'/identity)
                    source.load()
                    seed_terminal_ocr(store,identity,source,configuration,self.models)
            store.publishing=True
            run_document_v2(store,self.models,progress=lambda message: log.info('%s %s',document_id,message))
            store.drain()
            self.repo.complete(document_id,final_result(store))

    def verify_cleanup(self):
        verify_previous_cleanup(self.repo.root / "worker-runs")
        for directory in (self.repo.root / "documents").glob("*/pipeline/runs"):
            verify_previous_cleanup(directory)

    def run_available(self, *, wait_idle=True):
        first=self.repo.next_work()
        if first is None:
            return False
        active_doc=first[1]['id']
        run_directory=self.repo.root/'worker-runs'/uuid4().hex
        gpu = ExitStack()
        try:
            gpu.enter_context(exclusive_lock(self.settings.gpu_lock))
        except RuntimeError as exc:
            if str(exc).startswith('Already in use:'):
                return False
            raise
        try:
            with gpu:
                self.verify_cleanup()
                # A phase is kept loaded across queued images, but never across Qwen.
                with ExitStack() as phases:
                    paddle=False
                    idle_started=None
                    while True:
                        work=self.repo.next_work()
                        if work is None:
                            if paddle and wait_idle:
                                idle_started=idle_started or time.monotonic()
                                if time.monotonic()-idle_started < self.settings.idle_seconds:
                                    time.sleep(min(.25,self.settings.idle_seconds))
                                    continue
                            return True
                        idle_started=None
                        kind,doc,image=work
                        active_doc=doc['id']
                        if kind=='assemble':
                            phases.close()  # Cleanup failure prevents Qwen startup.
                            self.assemble(doc)
                            return True
                        store=self.ocr_store(doc,image)
                        self.repo.image_status(doc['id'],image['id'],'PROCESSING')
                        self.repo.progress(doc['id'],'OCR',image['id'])
                        if store.state['images'][image['id']]['ocr'] is None:
                            if not paddle:
                                phases.enter_context(self.models.phase('paddle',run_directory))
                                paddle=True
                            run_ocr(store,self.models,image['id'])
                        self.repo.image_status(doc['id'],image['id'],'COMPLETED')
                        self.repo.progress(doc['id'],'OCR',image['id'])
        except KeyboardInterrupt:
            raise
        except Exception:
            # Technical details remain on the protected machine; API gets safe text.
            log.exception('Document execution failed: %s',active_doc)
            self.repo.fail(active_doc)
            raise

    def serve(self):
        stop=threading.Event()
        def heartbeat():
            while not stop.is_set():
                try:
                    self.repo.heartbeat()
                except Exception:
                    log.exception('Worker heartbeat failed')
                stop.wait(5)
        def terminate(_signal,_frame):
            raise KeyboardInterrupt('Service termination')
        previous=signal.signal(signal.SIGTERM,terminate)
        try:
            with exclusive_lock(self.repo.root/'.worker.lock'):
                self.verify_cleanup()
                thread=threading.Thread(target=heartbeat,daemon=True)
                thread.start()
                try:
                    while True:
                        if not self.run_available():
                            time.sleep(.5)
                finally:
                    stop.set()
                    thread.join(timeout=6)
        finally:
            signal.signal(signal.SIGTERM,previous)
