"""Discover remaining cached cases and prepare cache-only evaluator compatibility."""

import hashlib
import json
from pathlib import Path
import re


SUBMODULE_PREPARE = '''import json,pathlib,subprocess
root=pathlib.Path('/testbed')
def git(path,*a): return subprocess.check_output(['git','-C',str(path)]+list(a)).decode().strip()
base=BASE_COMMIT_VALUE
subprocess.check_call(['git','-C',str(root),'reset','--hard',base])
records=[]
for line in git(root,'ls-tree','-r','HEAD').splitlines():
 meta,name=line.split('\\t',1)
 if not meta.startswith('160000 '): continue
 expected=meta.split()[2]
 path=(root/name).resolve()
 assert root in path.parents
 entry=path/'.git'
 if not entry.exists(): raise RuntimeError('Missing cached submodule Git entry: '+name)
 subprocess.check_call(['git','-C',str(path),'cat-file','-e',expected+'^{commit}'])
 subprocess.check_call(['git','-C',str(path),'reset','--hard',expected])
 subprocess.check_call(['git','-C',str(path),'clean','-fd'])
 assert not git(path,'status','--porcelain')
 assert git(path,'rev-parse','HEAD')==expected
 git(path,'diff','--exit-code',expected,'--')
 records.append({'path':name,'commit':expected,'tree':git(path,'rev-parse','HEAD^{tree}')})
 # The parent Git storage is removed by the sealed solver preflight later.
 # Remove this entry now, so baseline source remains materialized without a second repository.
 assert entry.is_file(), 'Only cached gitfile submodules supported'
 entry.unlink()
assert not git(root,'status','--porcelain')
print('BASELINE_SUBMODULES='+json.dumps(records))
'''


def remaining(inventory, previous_ids, excluded):
    return [row['instance_id'] for row in inventory
            if row['instance_id'] not in set(previous_ids) | set(excluded)]


def cached_requirements(client, image, record):
    """Get exact Git blobs, before solver history cleanup; never network access."""
    if record['repo'] != 'django/django':
        return None
    commit = record.get('environment_setup_commit') or record['base_commit']
    first = 'tests/requirements/py3.txt'
    files = {}
    container = client.containers.create(image=image, command='sleep 600', network_mode='none')
    try:
        container.start()
        pending = [first]
        while pending:
            path = pending.pop(0)
            if path in files:
                continue
            if not re.fullmatch(r'[A-Za-z0-9_./-]+', path) or '..' in Path(path).parts:
                raise ValueError('Unexpected dependency path')
            result = container.exec_run(['git', '-C', '/testbed', 'show', commit+':'+path])
            if result.exit_code:
                raise RuntimeError('Exact cached dependency blob missing: '+commit+':'+path)
            text = result.output.decode()
            files[path] = text
            for line in text.splitlines():
                if line.strip().startswith('-r'):
                    pending.append(str(Path(path).parent / line.strip()[2:].strip()))
        return {'repo': record['repo'], 'commit': commit, 'files': files,
                'sha256': {p: hashlib.sha256(t.encode()).hexdigest() for p, t in files.items()},
                'source': 'Exact cached Git blobs, not a reconstructed pip freeze'}
    finally:
        container.remove(force=True)


def prepare_remaining(output, previous, excluded, grader_python, reuse=None):
    import docker
    import pandas as pd
    from evaluation.analysis.mini_cached_screen_20261120 import run

    output.mkdir(parents=True, exist_ok=False)
    old = json.loads((previous / 'plan.json').read_text())
    rows = pd.read_excel(run.PROJECT / 'evaluation/benchmarks/swe_bench/data/full_summaries_verified.xlsx',
                        dtype={'version': str}).fillna('').to_dict('records')
    client = docker.from_env()
    inventory = []
    try:
        for row in rows:
            iid = row['instance_id']
            images = []
            for name in (f'sweb.eval.x86_64.{iid}:latest',
                         'xingyaoww/sweb.eval.x86_64.'+iid.replace('__', '_s_')+':latest'):
                try:
                    image = client.images.get(name)
                    images.append({'name': name, 'id': image.id})
                except docker.errors.ImageNotFound:
                    pass
            if images:
                inventory.append({'instance_id': iid, 'images': images})
        ids = remaining(inventory, [c['instance_id'] for c in old['cases']], excluded)
        report = {'at': run.now(), 'previous_plan': str(previous / 'plan.json'),
                  'previous_plan_sha256': run.sha(previous / 'plan.json'),
                  'previous_ids': [c['instance_id'] for c in old['cases']],
                  'excluded_ids': excluded, 'selected_ids': ids, 'inventory': inventory,
                  'model_calls': 0, 'image_overrides': {}, 'dependency_caches': {}}
        run.dump(output / 'remaining_preparation.json', report)
        canonical = run.canonical_records()
        records = {row['instance_id']: canonical.get(row['instance_id'], (row,))[0] for row in rows}
        if reuse:
            prior = json.loads((reuse / 'remaining_preparation.json').read_text())
            assert prior['selected_ids'] == ids
            assert prior['previous_plan_sha256'] == report['previous_plan_sha256']
            assert set(prior['image_overrides']) == set(ids)
            for iid, tag in prior['image_overrides'].items():
                provenance = json.loads((reuse / 'cache_preparation' / iid / 'provenance.json').read_text())
                assert client.images.get(tag).id == provenance['derived_image_id']
            caches = {}
            for iid, tag in prior['image_overrides'].items():
                cache = cached_requirements(client, client.images.get(tag).id, records[iid])
                if cache:
                    caches[iid] = cache
            report.update(image_overrides=prior['image_overrides'], dependency_caches=caches,
                          reused_cache_preparation=str(reuse))
            run.dump(output / 'remaining_preparation.json', report)
            run.prepare(output / 'experiment', ids, grader_python,
                        image_overrides=report['image_overrides'], dependency_caches=report['dependency_caches'])
            return
        by_id = {r['instance_id']: r for r in rows}
        by_cached = {r['instance_id']: r for r in inventory}
        build_root = output / 'cache_preparation'
        build_root.mkdir()
        for iid in ids:
            row = by_id[iid]
            source = by_cached[iid]['images'][0]
            build = build_root / iid
            build.mkdir()
            # Never replace historical image tags: every remaining case gets a new local tag.
            tag = 'mini-screen-cache/'+iid+':'+hashlib.sha256(str(output).encode()).hexdigest()[:10]
            code = SUBMODULE_PREPARE.replace('BASE_COMMIT_VALUE', repr(row['base_commit']))
            (build / 'normalize_submodules.py').write_text(code)
            container = client.containers.create(image=source['id'], command='sleep 600', network_mode='none')
            try:
                container.start()
                result = container.exec_run(['/opt/miniconda3/envs/testbed/bin/python', '-c', code])
                (build / 'preparation.log').write_bytes(result.output)
                if result.exit_code:
                    raise RuntimeError('Baseline cache preparation failed; inspect '+str(build / 'preparation.log'))
                repository, tag_name = tag.rsplit(':', 1)
                image = container.commit(repository=repository, tag=tag_name,
                                          changes=['LABEL ambig_screen_preparation="baseline-submodules-only"'])
            finally:
                container.remove(force=True)
            cache = cached_requirements(client, image.id, records[iid])
            report['image_overrides'][iid] = tag
            if cache:
                report['dependency_caches'][iid] = cache
            run.dump(build / 'provenance.json', {'base_image': source, 'derived_image': tag,
                     'derived_image_id': image.id,
                     'command': 'Docker SDK: network=none container exec baseline script; commit to fresh tag',
                     'policy': 'Restore only submodules to base gitlinks; no repair, test or dependency patch',
                     'dependency_cache': cache})
            run.dump(output / 'remaining_preparation.json', report)
            print('CACHE READY', iid, flush=True)
    finally:
        client.close()
    # Existing output contains only preparation evidence. prepare() requires a fresh child directory.
    run.prepare(output / 'experiment', ids, grader_python,
                image_overrides=report['image_overrides'], dependency_caches=report['dependency_caches'])
