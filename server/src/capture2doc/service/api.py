"""HTTP ingress and replayable SSE. This process never imports GPU libraries."""
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sse_starlette import EventSourceResponse, ServerSentEvent
from starlette.requests import ClientDisconnect

from capture2doc.pipeline.store import exclusive_lock, valid_id
from .repository import Repository, ServiceError
from .settings import Settings


class CreateDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    clientTaskId: str = Field(min_length=1, max_length=128)


class FinalizeDocument(BaseModel):
    model_config = ConfigDict(extra='forbid')
    pageIds: list[str] = Field(min_length=1, max_length=100)

    @field_validator('pageIds')
    @classmethod
    def identities(cls, values):
        for value in values:
            valid_id(value)
        return values


class DocumentResult(BaseModel):
    documentId: str
    schemaVersion: Literal['0.1']
    status: Literal['IDLE', 'OCR', 'ASSEMBLING', 'VALIDATING', 'COMPLETED', 'FAILED']
    title: str | None
    wordCount: int | None
    needsReview: Literal[False]
    c2dXml: str | None
    sha256: str | None
    message: str | None = None

class CreatedDocument(BaseModel):
    documentId: str

class UploadedImage(BaseModel):
    pageId: str
    sha256: str

class FinalizedDocument(BaseModel):
    accepted: Literal[True]


def check_jpeg(path: Path, max_edge: int):
    from PIL import Image, UnidentifiedImageError
    try:
        with Image.open(path) as image:
            if image.format != 'JPEG':
                raise ServiceError(415, '只接受 JPEG 图片')
            if min(image.size) <= 0 or max(image.size) > max_edge:
                raise ServiceError(413, '图片实际尺寸超出限制')
            image.verify()
        with Image.open(path) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ServiceError(400, 'JPEG 图片无法完整解码') from None


def create_app(settings: Settings | None = None):
    settings = settings or Settings.load()
    repo = Repository(settings)
    connections = Counter()

    @asynccontextmanager
    async def lifespan(app):
        with exclusive_lock(repo.root / '.api.lock'):
            await anyio.to_thread.run_sync(repo.recover_uploads)
            yield

    app = FastAPI(title='Capture2Doc', version='1.0', docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.repository = repo
    app.state.stream_connections = connections

    @app.exception_handler(ServiceError)
    async def service_error(request, exc):
        headers = {'WWW-Authenticate':'Bearer'} if exc.status == 401 else None
        return JSONResponse({'message':exc.message}, status_code=exc.status, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return JSONResponse({'message':'请求字段缺失或格式不正确'}, status_code=422)

    @app.middleware('http')
    async def json_limit(request, call_next):
        # JSON commands must have a bounded length; upload streams have their own cap.
        if request.method == 'POST':
            try:
                size = int(request.headers.get('content-length', '-1'))
            except ValueError:
                size = -1
            if not 0 <= size <= 65536:
                return JSONResponse({'message':'JSON 请求必须提供有效长度且不超过 64 KiB'}, status_code=413)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def authenticated(authorization: str | None = Header(default=None)):
        if not authorization or not authorization.startswith('Bearer '):
            raise ServiceError(401,'需要设备访问令牌')
        return repo.authenticate(authorization[7:])

    @app.get('/internal/health', include_in_schema=False)
    def health():
        with repo.connection() as db:
            db.execute('SELECT 1')
        return {'status':'ok'}

    @app.get('/openapi.json', include_in_schema=False)
    def openapi(_=Depends(authenticated)):
        schema = app.openapi()
        schema.setdefault('components',{}).setdefault('securitySchemes',{})['DeviceToken'] = {'type':'http','scheme':'bearer'}
        schema['security']=[{'DeviceToken':[]}]
        return schema

    @app.post('/v1/documents', response_model=CreatedDocument, responses={201: {'model': CreatedDocument}})
    def create(body: CreateDocument, idempotency_key: str = Header(), _=Depends(authenticated)):
        if idempotency_key != body.clientTaskId:
            raise ServiceError(409,'创建幂等键不匹配')
        identity, fresh = repo.create_document(body.clientTaskId)
        return JSONResponse({'documentId':identity},status_code=201 if fresh else 200)

    @app.put('/v1/documents/{document_id}/pages/{image_id}', response_model=UploadedImage,
             responses={201: {'model': UploadedImage}},
             openapi_extra={'requestBody': {'required': True, 'content': {'image/jpeg': {'schema': {'type': 'string', 'format': 'binary'}}}}})
    async def upload(document_id: str, image_id: str, request: Request,
                     x_content_sha256: str = Header(), _=Depends(authenticated)):
        try:
            valid_id(image_id)
        except ValueError:
            raise ServiceError(400,'图片 ID 格式不正确') from None
        sha = x_content_sha256.lower()
        if not re.fullmatch('[0-9a-f]{64}',sha):
            raise ServiceError(400,'图片摘要格式不正确')
        if request.headers.get('content-type','').split(';')[0].strip().lower() != 'image/jpeg':
            raise ServiceError(415,'只接受 image/jpeg')
        try:
            size = int(request.headers.get('content-length',str(settings.max_upload_bytes)))
        except ValueError:
            raise ServiceError(400,'图片长度不正确') from None
        reservation, old = await anyio.to_thread.run_sync(repo.reserve_upload,document_id,image_id,sha,size)
        file = None
        try:
            if reservation:
                file = (repo.root / reservation['path']).open('xb')
            digest, received = hashlib.sha256(), 0
            with anyio.fail_after(settings.upload_timeout_seconds):
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > size or received > settings.max_upload_bytes:
                        raise ServiceError(413,'图片超出声明大小或上传限制')
                    digest.update(chunk)
                    if file:
                        file.write(chunk)
            if not received or digest.hexdigest() != sha:
                raise ServiceError(400,'图片内容与 SHA-256 不匹配')
            if file:
                file.flush()
                os.fsync(file.fileno())
                file.close()
                file = None
                await anyio.to_thread.run_sync(check_jpeg,repo.root / reservation['path'],settings.max_image_edge)
                await anyio.to_thread.run_sync(repo.finish_upload,reservation,received)
            elif received != old['bytes']:
                raise ServiceError(409,'重复图片长度不匹配')
            return JSONResponse({'pageId':image_id,'sha256':sha},status_code=200 if old else 201)
        except ClientDisconnect:
            return Response(status_code=499)
        except TimeoutError:
            raise ServiceError(408,'图片上传超时，请重试') from None
        finally:
            if file:
                file.close()
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(repo.cancel_upload,reservation)

    @app.post('/v1/documents/{document_id}/finalize',status_code=202, response_model=FinalizedDocument)
    def finalize(document_id: str, body: FinalizeDocument, idempotency_key: str = Header(), _=Depends(authenticated)):
        if idempotency_key != repo.get(document_id)['client_key'] + '-finalize':
            raise ServiceError(409,'提交幂等键不匹配')
        repo.finalize(document_id,body.pageIds)
        return {'accepted':True}

    @app.get('/v1/documents/{document_id}', response_model=DocumentResult, response_model_exclude_unset=True)
    def get_document(document_id: str, _=Depends(authenticated)):
        return repo.public(document_id)

    @app.get('/v1/documents/{document_id}/events', responses={200:{'content':{'text/event-stream':{}}}})
    async def events(document_id: str, request: Request, last_event_id: str | None = Header(default=None),
                     device=Depends(authenticated)):
        cursor, snapshot = await anyio.to_thread.run_sync(repo.snapshot,document_id)
        fresh = last_event_id is None
        if not fresh:
            if not re.fullmatch(r'\d{1,18}',last_event_id):
                raise ServiceError(409,'事件游标无效')
            cursor = int(last_event_id)
            pending = await anyio.to_thread.run_sync(repo.events,document_id,cursor)
            if not pending and snapshot['status'] in ('COMPLETED','FAILED'):
                return Response(status_code=204)
        key=(device,document_id)
        if connections[key] >= settings.max_streams_per_device_document:
            raise ServiceError(429,'同一设备的文档连接数已达上限')
        connections[key] += 1

        async def stream():
            nonlocal cursor
            try:
                if fresh:
                    yield ServerSentEvent(event='document.snapshot',id=str(cursor),data=json.dumps(snapshot,ensure_ascii=False))
                    if snapshot['status'] in ('COMPLETED','FAILED'):
                        return
                while True:
                    if not await anyio.to_thread.run_sync(repo.token_active,device):
                        return
                    rows=await anyio.to_thread.run_sync(repo.events,document_id,cursor)
                    for seq,kind,data in rows:
                        cursor=seq
                        yield ServerSentEvent(event=kind,id=str(seq),data=json.dumps(data,ensure_ascii=False))
                        if kind in ('document.completed','document.failed'):
                            return
                    await anyio.sleep(settings.poll_seconds)
            finally:
                connections[key]-=1
                if not connections[key]:
                    del connections[key]

        return EventSourceResponse(stream(),ping=settings.heartbeat_seconds,
            send_timeout=settings.send_timeout_seconds,
            headers={'X-Accel-Buffering':'no','Cache-Control':'no-store'})

    return app
