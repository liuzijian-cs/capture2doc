"""Durable service records and idempotent checkpoint-outbox consumption."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from .events import patches
from .settings import Settings


class ServiceError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def milliseconds():
    return time.time_ns() // 1_000_000


class Repository:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_root
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        (self.root / "tmp").mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "service.sqlite3"
        with self.connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise RuntimeError("Unsupported service database version")
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tokens (
                  id TEXT PRIMARY KEY, name TEXT NOT NULL, digest TEXT UNIQUE NOT NULL,
                  created INTEGER NOT NULL, revoked INTEGER);
                CREATE TABLE IF NOT EXISTS documents (
                  id TEXT PRIMARY KEY, client_key TEXT UNIQUE NOT NULL, created INTEGER NOT NULL,
                  finalized INTEGER, final_order TEXT, status TEXT NOT NULL DEFAULT 'IDLE',
                  result TEXT, message TEXT, preview TEXT NOT NULL DEFAULT '[]',
                  revision INTEGER NOT NULL DEFAULT 0, event_seq INTEGER NOT NULL DEFAULT 0,
                  progress TEXT NOT NULL DEFAULT '{}');
                CREATE TABLE IF NOT EXISTS images (
                  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  id TEXT NOT NULL, sha256 TEXT NOT NULL, path TEXT NOT NULL, bytes INTEGER NOT NULL,
                  created INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'QUEUED',
                  PRIMARY KEY(document_id,id));
                CREATE TABLE IF NOT EXISTS uploads (
                  id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  image_id TEXT NOT NULL, sha256 TEXT NOT NULL, reserved INTEGER NOT NULL,
                  path TEXT NOT NULL, UNIQUE(document_id,image_id));
                CREATE TABLE IF NOT EXISTS events (
                  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  seq INTEGER NOT NULL, kind TEXT NOT NULL, data TEXT NOT NULL,
                  PRIMARY KEY(document_id,seq));
                CREATE TABLE IF NOT EXISTS receipts (
                  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  source_id TEXT NOT NULL, PRIMARY KEY(document_id,source_id));
                CREATE TABLE IF NOT EXISTS versions (
                  document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                  block_id TEXT NOT NULL, version INTEGER NOT NULL, xml TEXT NOT NULL, active INTEGER NOT NULL,
                  PRIMARY KEY(document_id,block_id));
                CREATE TABLE IF NOT EXISTS runtime (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
        os.chmod(self.path, 0o600)

    @contextmanager
    def connection(self, *, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def document_root(self, document_id):
        # Only server-generated IDs may select a storage directory.
        if len(document_id) != 32 or any(c not in "0123456789abcdef" for c in document_id):
            raise ServiceError(404, "文档不存在")
        return self.root / "documents" / document_id

    def _document(self, db, document_id):
        row = db.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise ServiceError(404, "文档不存在")
        return row

    def create_token(self, name):
        token = "c2d_" + secrets.token_urlsafe(32)
        identity = uuid4().hex
        with self.connection(write=True) as db:
            db.execute("INSERT INTO tokens VALUES (?,?,?,?,NULL)",
                       (identity, name, hashlib.sha256(token.encode()).hexdigest(), milliseconds()))
        return identity, token

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 256:
            raise ServiceError(401, "访问令牌无效")
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self.connection() as db:
            row = db.execute("SELECT id FROM tokens WHERE digest=? AND revoked IS NULL", (digest,)).fetchone()
        if not row:
            raise ServiceError(401, "访问令牌无效或已撤销")
        return row[0]

    def token_active(self, identity):
        with self.connection() as db:
            return db.execute("SELECT 1 FROM tokens WHERE id=? AND revoked IS NULL", (identity,)).fetchone() is not None

    def tokens(self):
        with self.connection() as db:
            return [dict(r) for r in db.execute("SELECT id,name,created,revoked FROM tokens ORDER BY created")]

    def revoke(self, identity):
        with self.connection(write=True) as db:
            if not db.execute("UPDATE tokens SET revoked=? WHERE id=?", (milliseconds(), identity)).rowcount:
                raise ServiceError(404, "令牌不存在")

    def create_document(self, key):
        with self.connection(write=True) as db:
            old = db.execute("SELECT id FROM documents WHERE client_key=?", (key,)).fetchone()
            if old:
                return old[0], False
            count = db.execute("SELECT count(*) FROM documents WHERE status NOT IN ('COMPLETED','FAILED')").fetchone()[0]
            if count >= self.settings.max_active_documents:
                raise ServiceError(429, "未完成文档数量已达上限")
            identity = uuid4().hex
            db.execute("INSERT INTO documents(id,client_key,created) VALUES (?,?,?)", (identity, key, milliseconds()))
            return identity, True

    def used_bytes(self):
        return sum(p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink())

    def reserve_upload(self, document_id, image_id, sha256, size):
        if size <= 0 or size > self.settings.max_upload_bytes:
            raise ServiceError(413, "图片大小超出限制")
        with self.connection(write=True) as db:
            doc = self._document(db, document_id)
            old = db.execute("SELECT * FROM images WHERE document_id=? AND id=?", (document_id,image_id)).fetchone()
            if old:
                if old['sha256'] != sha256:
                    raise ServiceError(409, "相同图片 ID 对应不同内容")
                # API still hashes the supplied bytes before acknowledging a retry.
                return None, dict(old)
            if doc['finalized'] is not None or doc['status'] in ('COMPLETED','FAILED'):
                raise ServiceError(409, "文档已冻结或停止，不能新增图片")
            if db.execute("SELECT 1 FROM uploads WHERE document_id=? AND image_id=?", (document_id,image_id)).fetchone():
                raise ServiceError(429, "该图片正在接收，请稍后重试")
            count = db.execute("SELECT count(*) FROM images WHERE document_id=?", (document_id,)).fetchone()[0]
            count += db.execute("SELECT count(*) FROM uploads WHERE document_id=?", (document_id,)).fetchone()[0]
            if count >= self.settings.max_images:
                raise ServiceError(413, "文档图片数量超出限制")
            reserved = db.execute("SELECT coalesce(sum(reserved),0) FROM uploads").fetchone()[0]
            if self.used_bytes() + reserved + size > self.settings.max_data_bytes:
                raise ServiceError(507, "数据目录容量不足")
            if shutil.disk_usage(self.root).free - reserved - size < self.settings.min_free_bytes:
                raise ServiceError(507, "磁盘剩余空间不足")
            uid = uuid4().hex
            path = f"tmp/{uid}.upload"
            db.execute("INSERT INTO uploads VALUES (?,?,?,?,?,?)", (uid,document_id,image_id,sha256,size,path))
            return {'id':uid, 'path':path, 'reserved':size}, None

    def cancel_upload(self, reservation):
        if not reservation:
            return
        with self.connection(write=True) as db:
            db.execute("DELETE FROM uploads WHERE id=?", (reservation['id'],))
        (self.root / reservation['path']).unlink(missing_ok=True)

    def finish_upload(self, reservation, actual_size):
        with self.connection(write=True) as db:
            upload = db.execute("SELECT * FROM uploads WHERE id=?", (reservation['id'],)).fetchone()
            if upload is None:
                raise ServiceError(409, "上传已失效，请重试")
            doc = self._document(db, upload['document_id'])
            if doc['finalized'] is not None or doc['status'] in ('FAILED','COMPLETED'):
                raise ServiceError(409, "文档已冻结，不能新增图片")
            directory = self.document_root(doc['id']) / "uploads"
            directory.mkdir(parents=True, exist_ok=True)
            dest = directory / (upload['id'] + '.jpg')
            os.replace(self.root / upload['path'], dest)
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
            db.execute("INSERT INTO images(document_id,id,sha256,path,bytes,created) VALUES (?,?,?,?,?,?)",
                       (doc['id'],upload['image_id'],upload['sha256'],str(dest.relative_to(self.root)),actual_size,milliseconds()))
            db.execute("DELETE FROM uploads WHERE id=?", (upload['id'],))

    def recover_uploads(self):
        # Caller owns API process lock. No other process writes temporary uploads.
        with self.connection(write=True) as db:
            db.execute("DELETE FROM uploads")
        for p in (self.root / 'tmp').glob('*.upload'):
            p.unlink()
        with self.connection() as db:
            paths = {r[0] for r in db.execute('SELECT path FROM images')}
        for p in (self.root / 'documents').glob('*/uploads/*.jpg'):
            if str(p.relative_to(self.root)) not in paths:
                p.unlink()  # File renamed before a database transaction rolled back.

    def finalize(self, document_id, order):
        with self.connection(write=True) as db:
            doc = self._document(db, document_id)
            if doc['final_order'] is not None:
                if json.loads(doc['final_order']) != order:
                    raise ServiceError(409, "最终图片顺序已冻结")
                return
            ids = {r[0] for r in db.execute("SELECT id FROM images WHERE document_id=?", (document_id,))}
            if not order or len(order) != len(set(order)) or any(i not in ids for i in order):
                raise ServiceError(409, "最终列表为空、重复或包含未上传图片")
            if doc['status'] == 'FAILED':
                raise ServiceError(409, "文档已停止")
            db.execute("UPDATE documents SET final_order=?,finalized=? WHERE id=?", (encoded(order),milliseconds(),document_id))

    def get(self, document_id):
        with self.connection() as db:
            return dict(self._document(db, document_id))

    def images(self, document_id):
        with self.connection() as db:
            self._document(db, document_id)
            return [dict(r) for r in db.execute("SELECT * FROM images WHERE document_id=? ORDER BY created,rowid", (document_id,))]

    def public(self, document_id):
        doc = self.get(document_id)
        if doc['result'] is not None and doc['status'] == 'COMPLETED':
            return json.loads(doc['result'])
        result = {'documentId':document_id, 'schemaVersion':'0.1', 'status':doc['status'],
                  'title':None, 'wordCount':None, 'needsReview':False, 'c2dXml':None, 'sha256':None}
        if doc['status'] == 'FAILED':
            result['message'] = doc['message']
        return result

    def _event(self, db, document_id, kind, data):
        seq = self._document(db, document_id)['event_seq'] + 1
        payload = {**data, 'documentId':document_id, 'publishedAtUnixMs':milliseconds()}
        db.execute("INSERT INTO events VALUES (?,?,?,?)", (document_id,seq,kind,encoded(payload)))
        db.execute("UPDATE documents SET event_seq=? WHERE id=?", (seq,document_id))

    def _preview(self, db, doc, desired):
        old = json.loads(doc['preview'])
        version_rows = {r['block_id']:r for r in db.execute('SELECT * FROM versions WHERE document_id=?', (doc['id'],))}
        new = []
        if len({b['blockId'] for b in desired}) != len(desired):
            raise RuntimeError('Duplicate preview identity')
        for b in desired:
            prior = version_rows.get(b['blockId'])
            version = 1 if prior is None else prior['version'] + int(not prior['active'] or prior['xml'] != b['xml'])
            new.append({**b, 'version':version})
        revision = doc['revision']
        for patch in patches(old, new, revision):
            self._event(db,doc['id'],'blocks.patch',patch)
            revision = patch['revision']
        db.execute('UPDATE versions SET active=0 WHERE document_id=?', (doc['id'],))
        for b in new:
            db.execute('INSERT INTO versions VALUES (?,?,?,?,1) ON CONFLICT(document_id,block_id) DO UPDATE SET version=excluded.version,xml=excluded.xml,active=1',
                       (doc['id'],b['blockId'],b['version'],b['xml']))
        db.execute('UPDATE documents SET preview=?,revision=? WHERE id=?', (encoded(new),revision,doc['id']))

    def consume(self, document_id, source_id, payload):
        with self.connection(write=True) as db:
            doc = self._document(db,document_id)
            if db.execute('SELECT 1 FROM receipts WHERE document_id=? AND source_id=?', (document_id,source_id)).fetchone():
                return
            if doc['status'] in ('COMPLETED','FAILED'):
                raise RuntimeError('Outbox delivered after terminal publication')
            if 'blocks' in payload:
                self._preview(db,doc,payload['blocks'])
            if 'progress' in payload:
                value = encoded(payload['progress'])
                if value != doc['progress']:
                    self._event(db,document_id,'document.progress',payload['progress'])
                    db.execute('UPDATE documents SET progress=?,status=? WHERE id=?', (value,payload['progress']['status'],document_id))
            db.execute('INSERT INTO receipts VALUES (?,?)',(document_id,source_id))

    def progress(self, document_id, status, current_image=None, completed_images=0):
        doc = self.get(document_id)
        rows = self.images(document_id)
        order = json.loads(doc['final_order']) if doc['final_order'] else [r['id'] for r in rows]
        payload = {'progress': {'status':status, 'currentImageId':current_image,
                   'completedOcrImages':sum(r['id'] in order and r['status']=='COMPLETED' for r in rows),
                   'completedImages':completed_images,'totalImages':len(order)}}
        self.consume(document_id,uuid4().hex,payload)

    def image_status(self, document_id, image_id, status):
        with self.connection(write=True) as db:
            db.execute('UPDATE images SET status=? WHERE document_id=? AND id=?', (status,document_id,image_id))

    def complete(self, document_id, result):
        with self.connection(write=True) as db:
            doc = self._document(db,document_id)
            if doc['status'] == 'COMPLETED':
                if json.loads(doc['result']) != result:
                    raise RuntimeError('Final result changed')
                return
            if doc['status'] == 'FAILED':
                raise RuntimeError('Cannot publish a failed document')
            db.execute('UPDATE documents SET status=?,result=?,message=NULL WHERE id=?', ('COMPLETED',encoded(result),document_id))
            self._event(db,document_id,'document.completed',{'status':'COMPLETED','sha256':result['sha256']})

    def fail(self, document_id, message='服务端处理失败，请联系服务管理员。'):
        with self.connection(write=True) as db:
            doc = self._document(db,document_id)
            if doc['status'] in ('COMPLETED','FAILED'):
                return
            db.execute("UPDATE documents SET status='FAILED',message=? WHERE id=?", (message,document_id))
            self._event(db,document_id,'document.failed',{'status':'FAILED','message':message})

    def snapshot(self, document_id):
        with self.connection() as db:
            doc = self._document(db,document_id)
            data = {**json.loads(doc['progress']), 'documentId':document_id,'status':doc['status'],
                    'revision':doc['revision'],'blocks':json.loads(doc['preview'])}
            if doc['result']:
                data['sha256'] = json.loads(doc['result'])['sha256']
            if doc['status']=='FAILED':
                data['message']=doc['message']
            return doc['event_seq'],data

    def events(self, document_id, after, limit=32):
        with self.connection() as db:
            doc = self._document(db,document_id)
            if after < 0 or after > doc['event_seq']:
                raise ServiceError(409,'事件游标无效，请重新获取快照')
            return [(r['seq'],r['kind'],json.loads(r['data'])) for r in db.execute(
                'SELECT * FROM events WHERE document_id=? AND seq>? ORDER BY seq LIMIT ?', (document_id,after,limit))]

    def next_work(self):
        with self.connection() as db:
            docs = [dict(r) for r in db.execute("SELECT * FROM documents WHERE status NOT IN ('COMPLETED','FAILED') ORDER BY finalized,created,id")]
            all_images = [dict(r) for r in db.execute('SELECT * FROM images ORDER BY created,rowid')]
        by_doc = {d['id']:d for d in docs}
        for doc in sorted((d for d in docs if d['finalized'] is not None),key=lambda d:(d['finalized'],d['id'])):
            order=json.loads(doc['final_order'])
            images={i['id']:i for i in all_images if i['document_id']==doc['id']}
            for identity in order:
                if images[identity]['status']!='COMPLETED':
                    return 'ocr',doc,images[identity]
            return 'assemble',doc,None
        for image in all_images:
            doc=by_doc.get(image['document_id'])
            if doc and doc['finalized'] is None and image['status']!='COMPLETED':
                return 'ocr',doc,image
        return None

    def heartbeat(self):
        with self.connection(write=True) as db:
            db.execute("INSERT INTO runtime VALUES ('worker_heartbeat',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(milliseconds()),))

    def prune(self, document_id, execute=False):
        # Caller owns worker and API locks, so neither uploads nor inference can race deletion.
        with self.connection(write=True) as db:
            doc=self._document(db,document_id)
            if doc['status'] not in ('COMPLETED','FAILED'):
                raise ServiceError(409,'仅允许清理已结束文档')
            if execute:
                db.execute('DELETE FROM documents WHERE id=?',(document_id,))
        directory=self.document_root(document_id)
        if execute and directory.exists():
            shutil.rmtree(directory)
        return {'documentId':document_id,'path':str(directory),'deleted':execute}
