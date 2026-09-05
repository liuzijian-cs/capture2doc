from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path

from capture2doc.pipeline.store import exclusive_lock
from .repository import Repository
from .settings import Settings


def main(argv=None):
    parser=argparse.ArgumentParser(description='Capture2Doc HTTP service and single-GPU worker')
    parser.add_argument('--config',type=Path)
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('api','worker','token','storage'):
        command=commands.add_parser(name)
        command.add_argument('--config',type=Path,default=argparse.SUPPRESS)
        if name in ('token','storage'):
            actions=command.add_subparsers(dest='action',required=True)
            for action in (('create','list','revoke') if name=='token' else ('inspect','prune')):
                sub=actions.add_parser(action)
                sub.add_argument('--config',type=Path,default=argparse.SUPPRESS)
                if action=='create': sub.add_argument('--name',required=True)
                if action=='revoke': sub.add_argument('id')
                if action=='prune':
                    sub.add_argument('--document-id',required=True)
                    sub.add_argument('--execute',action='store_true')
    args=parser.parse_args(argv)
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(name)s %(message)s')
    settings=Settings.load(args.config)
    try:
        if args.command=='api':
            import uvicorn
            from .api import create_app
            uvicorn.run(create_app(settings),host=settings.host,port=settings.port,
                        proxy_headers=True,forwarded_allow_ips=settings.trusted_proxy,access_log=False)
        elif args.command=='worker':
            from .worker import Worker
            Worker(settings).serve()
        else:
            repo=Repository(settings)
            if args.command=='token':
                if args.action=='create':
                    identity,token=repo.create_token(args.name)
                    print(json.dumps({'id':identity,'token':token}))
                elif args.action=='list':
                    print(json.dumps(repo.tokens(),ensure_ascii=False,indent=2))
                else:
                    repo.revoke(args.id)
                    print(json.dumps({'revoked':args.id}))
            elif args.action=='inspect':
                print(json.dumps({'dataRoot':str(repo.root),'bytes':repo.used_bytes()},indent=2))
            else:
                with exclusive_lock(repo.root/'.worker.lock'), exclusive_lock(repo.root/'.api.lock'):
                    print(json.dumps(repo.prune(args.document_id,args.execute),ensure_ascii=False,indent=2))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        # CLI is a local administrative surface; tokens/contents are never in errors.
        logging.error('%s: %s',type(exc).__name__,exc)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
