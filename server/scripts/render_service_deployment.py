"""Render deployment templates from private machine configuration.

Generated files contain environment details and belong outside version control.
"""
from __future__ import annotations

import argparse
import ipaddress
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from capture2doc.service.settings import Settings


def systemd_value(value: str) -> str:
    if any(c in value for c in '\n\r\0'):
        raise ValueError('A deployment value cannot contain control characters')
    return value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')


def render(settings, *, checkout: Path, config: Path, user: str, output: Path,
           public_host=None, certificate=None, certificate_key=None):
    if user == 'root' or not re.fullmatch(r'[a-z_][a-z0-9_-]*[$]?', user):
        raise ValueError('A non-root system service account is required')
    backend = str(ipaddress.IPv4Address(settings.host))
    proxy = str(ipaddress.IPv4Address(settings.trusted_proxy))
    if backend == '0.0.0.0' or proxy == '0.0.0.0':
        raise ValueError('Specify concrete backend and proxy addresses in private configuration')
    server = checkout.resolve() / 'server'
    executable = server / '.venv/bin/capture2doc'
    if not executable.is_file() or not config.is_file():
        raise ValueError('Install service dependencies and create the private configuration first')
    if ':' in str(server):
        raise ValueError('The checkout path cannot contain the PATH separator')
    values = {'SERVICE_USER': user, 'SERVER_DIR': '"' + systemd_value(str(server)) + '"',
              'EXECUTABLE': '"' + systemd_value(str(executable)) + '"',
              'CONFIG_FILE': '"' + systemd_value(str(config.resolve())) + '"',
              'BIN_DIR': systemd_value(str(executable.parent)),
              'BACKEND_HOST': backend, 'TRUSTED_PROXY': proxy, 'PORT': str(settings.port)}
    names = ['capture2doc-api.service', 'capture2doc-worker.service', 'capture2doc-firewall.nft']
    if any((public_host, certificate, certificate_key)):
        if not all((public_host, certificate, certificate_key)):
            raise ValueError('Public host and both certificate paths must be supplied together')
        if len(public_host) > 253 or not re.fullmatch(r'[A-Za-z0-9.-]+', public_host):
            raise ValueError('Invalid public host')
        for value in (certificate, certificate_key):
            if not value.startswith('/') or any(c in value for c in '\n\r\0"\\$;{}'):
                raise ValueError('Certificate paths must be absolute literal proxy paths')
        values.update(PUBLIC_HOST=public_host, CERTIFICATE='"'+certificate+'"',
                      CERTIFICATE_KEY='"'+certificate_key+'"')
        names.append('openresty.conf')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    templates = Path(__file__).resolve().parents[1] / 'deploy'
    for name in names:
        text = (templates / name).read_text()
        for key, value in values.items():
            text = text.replace('@'+key+'@', value)
        if re.search(r'@[A-Z_]+@', text):
            raise ValueError('Unresolved deployment placeholder')
        path = output / name
        with path.open('w') as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(text)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--checkout', type=Path, required=True)
    parser.add_argument('--user', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--public-host')
    parser.add_argument('--certificate')
    parser.add_argument('--certificate-key')
    args = parser.parse_args()
    os.umask(0o077)
    render(Settings.load(args.config), checkout=args.checkout, config=args.config, user=args.user,
           output=args.output, public_host=args.public_host, certificate=args.certificate,
           certificate_key=args.certificate_key)
    print('Deployment files rendered; keep them outside Git.')


if __name__ == '__main__':
    main()
