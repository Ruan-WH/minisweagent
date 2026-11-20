"""Host-only official SWE-bench scoring. Never imported by a model worker."""

import hashlib
import importlib.metadata
import json
from pathlib import Path
from types import SimpleNamespace


def make_spec(case):
    from swebench.harness import utils
    from swebench.harness.test_spec import make_test_spec

    original_get = utils.requests.get
    cache = case.get('dependency_cache')
    if cache:
        assert cache['repo'] == case['evaluation_record']['repo']
        assert cache['commit'] == (case['evaluation_record'].get('environment_setup_commit')
                                   or case['evaluation_record']['base_commit'])
        assert all(hashlib.sha256(text.encode()).hexdigest() == cache['sha256'][path]
                   for path, text in cache['files'].items())
        prefix = 'https://raw.githubusercontent.com/'+cache['repo']+'/'+cache['commit']+'/'
        def exact_get(url, *args, **kwargs):
            if not url.startswith(prefix):
                raise RuntimeError('Unexpected dependency URL; no network fallback')
            path = url[len(prefix):]
            return SimpleNamespace(status_code=200 if path in cache['files'] else 404,
                                   text=cache['files'].get(path, ''))
        utils.requests.get = exact_get
    try:
        spec = make_test_spec(case['evaluation_record'])
    finally:
        utils.requests.get = original_get
    if case['image_name'] != spec.instance_image_key:
        class CachedImageSpec(type(spec)):
            @property
            def instance_image_key(self):
                return case['image_name']
        cached = CachedImageSpec(**spec.__dict__)
        assert cached.eval_script == spec.eval_script
        spec = cached
    return spec


def grade(case, folder):
    import docker
    from swebench.harness.run_evaluation import run_instance

    assert (folder / 'result.json').is_file(), 'Solver must have stopped'
    target = folder / 'official_eval'
    target.mkdir(exist_ok=False)
    prediction = json.loads((folder / 'predictions.json').read_text())
    assert prediction['model_patch'] == (folder / 'model.patch').read_text()
    row = case['evaluation_record']
    spec = make_spec(case)
    client = docker.from_env()
    try:
        image = client.images.get(spec.instance_image_key)
        assert image.id == case['image_id'], 'Solver and evaluator must use the frozen image'
        parent = client.images.get(spec.env_image_key)
        assert image.attrs['Created'] >= parent.attrs['Created'], 'Official harness would rebuild this cache'
        (target / 'dataset.json').write_text(json.dumps([row], ensure_ascii=False, indent=2))
        (target / 'prediction.json').write_text(json.dumps(prediction, ensure_ascii=False, indent=2))
        provenance = {'swebench_version': importlib.metadata.version('swebench'),
                      'entrypoint': 'swebench.harness.run_evaluation.run_instance',
                      'model_has_stopped': True, 'feedback_to_model': False,
                      'image_id': image.id, 'eval_script_sha256': hashlib.sha256(spec.eval_script.encode()).hexdigest(),
                      'image_name_override': case['image_name'],
                      'dependency_source': case.get('dependency_cache', {}).get('source', 'Official static environment spec'),
                      'empty_patch_policy': 'Run the same official single-instance entrypoint even for an empty diff',
                      'rm_image': False, 'force_rebuild': False, 'timeout': 1200}
        (target / 'provenance.json').write_text(json.dumps(provenance, indent=2))
        # Fresh cwd gives the unmodified harness independent logs for every arm.
        import os
        os.chdir(target)
        run_instance(spec, prediction, False, False, client, 'cached-screen', 1200)
        reports = list(Path('logs/run_evaluation').rglob('report.json'))
        if len(reports) != 1:
            raise RuntimeError('Official evaluation did not produce exactly one report; inspect grader.console.log')
        report = json.loads(reports[0].read_text())[case['instance_id']]
        (target / 'report.json').write_text(json.dumps(report, indent=2))
    finally:
        client.close()


def check_specs(cases):
    import docker

    client = docker.from_env()
    try:
        details = []
        for case in cases:
            spec = make_spec(case)
            image = client.images.get(spec.instance_image_key)
            assert image.id == case['image_id']
            parent = client.images.get(spec.env_image_key)
            assert image.attrs['Created'] >= parent.attrs['Created'], 'Official harness would rebuild this cache'
            details.append({'instance_id': case['instance_id'],
                            'instance_image': spec.instance_image_key, 'env_image': spec.env_image_key,
                            'target_tests': len(spec.FAIL_TO_PASS), 'regression_tests': len(spec.PASS_TO_PASS),
                            'eval_script_sha256': hashlib.sha256(spec.eval_script.encode()).hexdigest()})
        return {'passed': True, 'model_calls': 0,
                'version': importlib.metadata.version('swebench'), 'cases': details}
    finally:
        client.close()
