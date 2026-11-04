import hashlib
import io
import json
from pathlib import Path
import zipfile
import pytest
from diamond_mcp.demo_seed import build,restore,maybe_restore,REPOSITORY,REVISION
from diamond_mcp.engine import Config,Actor
from diamond_mcp.service import DiscoveryService


@pytest.fixture
def seed(tmp_path):
    source=tmp_path/'public';source.mkdir()
    archive=source/'bank-of-anthos.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('schema.sql','CREATE TABLE person (id INT,birthday DATE);')
        z.writestr('README.md','# Banking sample\nA public banking application used for discovery tests.\n')
    (source/'source.json').write_text(json.dumps(dict(sample='bank-of-anthos',repository=REPOSITORY,
        revision=REVISION,archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),license='Apache-2.0')))
    output=tmp_path/'seed'
    manifest=build(source,output,isolate=False)
    return output,manifest


def test_cold_state_restores_completed_discovery_without_network_or_analysis(tmp_path,seed,monkeypatch):
    source,manifest=seed
    target=tmp_path/'runtime'
    restored=restore(target,source)
    assert restored['restored'] and restored['files']==2
    engine=DiscoveryService(Config(target))
    actor=Actor('copilot',frozenset({'demo'}))
    app=engine.applications(actor,'demo')[0]
    assert app['current_analysis']==manifest['analysis_id']
    # All evidence exists immediately; no queued indexing or source import is needed.
    monkeypatch.setattr(engine.pipeline,'run',lambda *args,**kwargs:pytest.fail('Cold restore must not reanalyze'))
    report=engine.discover_application(actor,'demo','Bank of Anthos')
    assert report['analysis_id']==manifest['analysis_id']
    assert any(field['name']=='birthday' for field in report['fields'])
    private=engine.app(actor,'demo',app['id'])
    assert Path(private['source']).is_relative_to(target)
    assert Path(private['source']).is_dir()
    assert restore(target,source)==dict(restored=False,reason='existing_catalog_preserved')


def test_existing_client_catalog_is_never_replaced(tmp_path,seed):
    source,_=seed
    target=tmp_path/'client'
    engine=DiscoveryService(Config(target))
    actor=Actor('customer',frozenset({'demo'}))
    app=engine.register(actor,'demo','My existing application')['id']
    assert not restore(target,source)['restored']
    assert [a['id'] for a in engine.applications(actor,'demo')]==[app]


def test_seed_is_scoped_to_explicit_sqlite_token_demo(tmp_path,seed):
    source,_=seed
    settings=dict(ORIGINSPAN_DEMO_SEED=str(source),ORIGINSPAN_AUTH_MODE='demo-token',ORIGINSPAN_STORAGE_MODE='postgres')
    assert maybe_restore(settings,tmp_path/'postgres') is None
    settings.update(ORIGINSPAN_STORAGE_MODE='sqlite-demo',ORIGINSPAN_AUTH_MODE='oidc')
    assert maybe_restore(settings,tmp_path/'oidc') is None
    settings.update(ORIGINSPAN_AUTH_MODE='demo-token',ORIGINSPAN_PROJECT='client')
    assert maybe_restore(settings,tmp_path/'other') is None
    settings['ORIGINSPAN_PROJECT']='demo'
    assert maybe_restore(settings,tmp_path/'demo')['restored']
    assert settings['ORIGINSPAN_PUBLIC_DEMO_RESTORED']=='true'


def test_corrupt_or_stale_seed_fails_without_publishing_a_catalog(tmp_path,seed):
    source,_=seed
    manifest=json.loads((source/'seed.json').read_text())
    manifest['extraction_version']='old'
    (source/'seed.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='stale'):
        restore(tmp_path/'runtime',source)
    assert not (tmp_path/'runtime/state.sqlite').exists()


def test_seed_archive_must_match_committed_checksum(tmp_path):
    source=tmp_path/'bad';source.mkdir()
    (source/'source.json').write_text(json.dumps(dict(sample='bank-of-anthos',repository=REPOSITORY,
        revision=REVISION,archive_sha256='0'*64)))
    (source/'bank-of-anthos.zip').write_bytes(b'not the approved archive')
    with pytest.raises(ValueError,match='checksum'):
        build(source,tmp_path/'seed',isolate=False)
