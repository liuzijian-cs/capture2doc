import importlib.util
from pathlib import Path
import re

import pytest

from capture2doc.service.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('deployment', ROOT/'server/scripts/render_service_deployment.py')
deployment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deployment)


def setup(tmp_path):
    checkout = tmp_path/'checkout with spaces'
    executable = checkout/'server/.venv/bin/capture2doc'
    executable.parent.mkdir(parents=True)
    executable.write_text('')
    config = tmp_path/'private.toml'
    config.write_text('[service]\nhost="192.0.2.20"\ntrusted_proxy="192.0.2.10"\nport=11209\n')
    return checkout,config


def test_private_render_preserves_ingress_and_nonroot_units(tmp_path):
    checkout,config=setup(tmp_path)
    output=tmp_path/'private-output'
    deployment.render(Settings.load(config),checkout=checkout,config=config,user='serviceuser',output=output,
                      public_host='docs.example.test',certificate='/private/cert.pem',certificate_key='/private/key.pem')
    api=(output/'capture2doc-api.service').read_text()
    assert 'User=serviceuser' in api and 'Requires=capture2doc-firewall.service' in api
    assert f'WorkingDirectory="{checkout}/server"' in api
    assert f'--config "{config}"' in api
    firewall=(output/'capture2doc-firewall.nft').read_text()
    assert '192.0.2.10 tcp dport 11209 accept' in firewall
    assert 'tcp dport 11209 drop' in firewall
    proxy=(output/'openresty.conf').read_text()
    assert '192.0.2.20:11209' in proxy and 'proxy_buffering off' in proxy
    for path in output.iterdir():
        assert path.stat().st_mode & 0o777 == 0o600
        assert not re.search(r'@[A-Z_]+@',path.read_text())


@pytest.mark.parametrize('change',[{'user':'root'},{'user':'bad\nuser'},
    {'public_host':'unsafe;return 200;','certificate':'/cert','certificate_key':'/key'},
    {'public_host':'docs.example.test'},
    {'public_host':'docs.example.test','certificate':'/cert','certificate_key':'/$variable'}])
def test_renderer_rejects_unsafe_or_incomplete_values(tmp_path,change):
    checkout,config=setup(tmp_path)
    args=dict(checkout=checkout,config=config,user='serviceuser',output=tmp_path/'output')
    args.update(change)
    with pytest.raises(ValueError):deployment.render(Settings.load(config),**args)


def test_public_service_files_have_no_deployment_identity_or_credentials():
    paths=[ROOT/'docs/server_service.md',ROOT/'docs/server_service_validation.md',ROOT/'docs/server_android_handoff.md']
    paths+=list((ROOT/'server/deploy').glob('*'))
    for path in paths:
        if not path.is_file():continue
        text=path.read_text()
        # Service templates may use localhost; documentation/test networks are examples.
        for value in re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b',text):
            assert value=='127.0.0.1',f'Environment IP in {path.name}'
        assert not re.search(r'BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY',text),path.name
        assert not re.search(r'c2d_[A-Za-z0-9_-]{40,}',text),path.name
        assert not re.search(r'/home/[A-Za-z][A-Za-z0-9_-]*/',text),path.name
    assert Settings().host == Settings().trusted_proxy == Settings().model_host == '127.0.0.1'
