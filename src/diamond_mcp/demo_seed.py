"""Build and restore only the bundled public banking demo, without runtime network I/O."""
from __future__ import annotations
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import uuid

REPOSITORY='https://github.com/GoogleCloudPlatform/bank-of-anthos'
REVISION='db35fea9fd090150e2398106aadb475576f80d94'


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def build(source,output,*,isolate=True):
    from .engine import Config,Actor
    from .service import DiscoveryService
    from .pipeline import EXTRACTION_VERSION
    from .catalog import Catalog
    source,output=Path(source).resolve(),Path(output).resolve()
    supplied=json.loads((source/'source.json').read_text())
    if supplied.get('sample')!='bank-of-anthos' or supplied.get('repository')!=REPOSITORY or supplied.get('revision')!=REVISION:
        raise ValueError('Only the pinned public banking demo can be bundled')
    archive=source/'bank-of-anthos.zip'
    if digest(archive)!=supplied.get('archive_sha256'):
        raise ValueError('Public demo archive checksum mismatch')
    output.mkdir(parents=True,exist_ok=False)
    previous={key:os.environ.get(key) for key in ('ORIGINSPAN_EXTERNAL_WORKERS','ORIGINSPAN_NEURAL_ENABLED')}
    os.environ.update(ORIGINSPAN_EXTERNAL_WORKERS='true',ORIGINSPAN_NEURAL_ENABLED='false')
    try:
        engine=DiscoveryService(Config(output))
        actor=Actor('bundled-public-demo',frozenset({'demo'}))
        app=engine.register(actor,'demo','Bank of Anthos')['id']
        engine.import_archive(actor,'demo',app,archive.read_bytes(),provenance=f'bundled-public:{REPOSITORY}@{REVISION}')
        job=engine.start(actor,'demo',app)['id']
        engine.pipeline.run('public-demo-build',job,isolate=isolate)
        result=engine.job(actor,'demo',app,job)
        if result['status']!='COMPLETED':
            raise ValueError('The bundled banking analysis must complete before image publication')
        engine.stop_workers()
        engine.store.engine.dispose()
        # SQLite backup includes committed WAL content and produces one portable file.
        with closing(sqlite3.connect(output/'state.sqlite')) as origin,closing(sqlite3.connect(output/'catalog.sqlite')) as backup:
            origin.backup(backup)
        manifest={**supplied,'project':'demo','application_id':app,'analysis_id':job,
            'schema':Catalog.VERSION,'extraction_version':EXTRACTION_VERSION,'original_state':str(output),
            'catalog_sha256':digest(output/'catalog.sqlite'),'snapshot':result['snapshot'],
            'evidence_count':result['evidence_count']}
        (output/'seed.json').write_text(json.dumps(manifest,indent=2)+'\n')
        # Only disposable builder-generated files are removed, never client sources.
        for name in ('state.sqlite','state.sqlite-wal','state.sqlite-shm'):
            (output/name).unlink(missing_ok=True)
        return manifest
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def copy_public_tree(source,target):
    """Resume a partial copy, but never overwrite a different file or follow a link."""
    source,target=Path(source),Path(target)
    if source.is_symlink() or source.is_junction():
        raise ValueError('Seed links are forbidden')
    for directory,dirs,names in os.walk(source,followlinks=False):
        parent=Path(directory)
        for name in dirs+names:
            path=parent/name
            if path.is_symlink() or path.is_junction():
                raise ValueError('Seed links are forbidden')
        destination=target/parent.relative_to(source)
        if destination.is_symlink() or destination.is_junction():
            raise ValueError('Seed destination links are forbidden')
        destination.mkdir(parents=True,exist_ok=True)
        for name in names:
            origin=parent/name;copied=destination/name
            if copied.is_symlink() or copied.is_junction():
                raise ValueError('Seed destination links are forbidden')
            if copied.exists():
                if digest(origin)!=digest(copied):
                    raise ValueError('Seed destination collision; existing files were preserved')
            else:
                shutil.copyfile(origin,copied)


def restore(state,seed,project='demo'):
    """Restore an empty ephemeral state before the API/worker starts. No customer-state replacement."""
    from .pipeline import EXTRACTION_VERSION
    from .catalog import Catalog
    state,seed=Path(state).resolve(),Path(seed).resolve()
    destination=state/'state.sqlite'
    if destination.exists():
        return {'restored':False,'reason':'existing_catalog_preserved'}
    manifest=json.loads((seed/'seed.json').read_text())
    if project!='demo' or manifest.get('project')!=project:
        raise ValueError('The public seed is scoped to the demo project only')
    if manifest.get('repository')!=REPOSITORY or manifest.get('revision')!=REVISION or manifest.get('sample')!='bank-of-anthos':
        raise ValueError('Unrecognized public demo seed')
    if manifest.get('schema')!=Catalog.VERSION or manifest.get('extraction_version')!=EXTRACTION_VERSION:
        raise ValueError('Public demo index is stale; rebuild the image')
    catalog=seed/'catalog.sqlite'
    if digest(catalog)!=manifest.get('catalog_sha256'):
        raise ValueError('Public demo catalog checksum mismatch')
    state.mkdir(parents=True,exist_ok=True)
    for name in ('imports','blobs'):
        copy_public_tree(seed/name,state/name)
    temporary=state/('.public-demo-'+uuid.uuid4().hex+'.sqlite')
    try:
        shutil.copyfile(catalog,temporary)
        old=Path(manifest['original_state'])
        def relocate(value):
            path=Path(value)
            if not path.is_relative_to(old):
                raise ValueError('Demo source reference escaped the bundled state')
            return str(state/path.relative_to(old))
        with closing(sqlite3.connect(temporary)) as db:
            applications=db.execute("SELECT project,application,id,payload FROM discovery_objects WHERE kind='application'").fetchall()
            if len(applications)!=1 or applications[0][0]!='demo' or applications[0][1]!=manifest['application_id']:
                raise ValueError('Seed must contain exactly the public demo application')
            for scope,app,id_,raw in applications:
                item=json.loads(raw)
                if item['name']!='Bank of Anthos' or item.get('owner')!='bundled-public-demo':
                    raise ValueError('Seed is not the bundled public sample')
                item['source']=relocate(item['source'])
                db.execute("UPDATE discovery_objects SET payload=? WHERE project=? AND application=? AND kind='application' AND id=?",
                    (json.dumps(item),scope,app,id_))
            for id_,source,status in db.execute('SELECT id,source,state FROM d3_jobs').fetchall():
                if status!='COMPLETED':
                    raise ValueError('A demo seed cannot contain unfinished jobs')
                db.execute('UPDATE d3_jobs SET source=? WHERE id=?',(relocate(source),id_))
            if db.execute('SELECT COUNT(*) FROM d3_interpretations').fetchone()[0]:
                raise ValueError('A public seed cannot contain AI interpretations or approvals')
            db.commit()
        # Publish catalog last: a cold start sees either no catalog or a complete one.
        if destination.exists():
            raise ValueError('Catalog appeared during demo restoration; refusing to replace it')
        os.replace(temporary,destination)
        return {'restored':True,'sample':'bank-of-anthos','analysis_id':manifest['analysis_id'],
                'files':manifest['snapshot']['files'],'evidence_count':manifest['evidence_count']}
    finally:
        temporary.unlink(missing_ok=True)


def maybe_restore(environ,state):
    seed=environ.get('ORIGINSPAN_DEMO_SEED','')
    if not seed or seed.lower() in ('off','false','none'):
        return None
    if environ.get('ORIGINSPAN_STORAGE_MODE')!='sqlite-demo' or environ.get('ORIGINSPAN_AUTH_MODE')!='demo-token':
        return None
    if environ.get('ORIGINSPAN_PROJECT','demo')!='demo':
        return None
    result=restore(state,seed)
    if result.get('restored'):
        environ['ORIGINSPAN_PUBLIC_DEMO_RESTORED']='true'
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['build'])
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    manifest=build(args.source,args.output)
    print(json.dumps(dict(event='public_demo_built',files=manifest['snapshot']['files'],evidence_count=manifest['evidence_count'])))


if __name__=='__main__':
    main()
