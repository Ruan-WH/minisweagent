"""Baseline-only disposable containers; no model access during preparation."""

import json
from pathlib import Path
import shlex


def python(env, code):
    return json.loads(env.checked('python -B -c ' + shlex.quote(code)))


def alignment(env, package):
    return python(env, '''import importlib,json,pathlib,subprocess,sys
p=pathlib.Path(importlib.import_module(PACKAGE).__file__).resolve()
assert str(p).startswith('/testbed/'+PACKAGE+'/'), str(p)
original=p.read_bytes()
try:
 p.write_bytes(original+b"\\n_MINI_ALIGNMENT_PROBE = 'visible'\\n")
 value=subprocess.check_output([sys.executable,'-B','-c',
  "import "+PACKAGE+"; print("+PACKAGE+"._MINI_ALIGNMENT_PROBE)"],cwd='/testbed').decode().strip()
 assert value=='visible'
finally:
 p.write_bytes(original)
print(json.dumps({'cwd':str(pathlib.Path.cwd()),'import_path':str(p),
 'python':sys.executable,'python_version':sys.version,'edit_visible':True,
 'restored_exact_bytes':p.read_bytes()==original}))
'''.replace('PACKAGE', repr(package)))


def setup(case):
    import docker
    from evaluation.benchmarks.swe_bench.mini_swe.runtime import initialize, start_environment

    client = docker.from_env()
    env = start_environment(case['image_id'], 120, 'none')
    try:
        startup = initialize(env, case)
        env.config.interpreter = ['timeout', '-s', 'KILL', '120', 'bash', '-o', 'pipefail', '-c']
        env.checked('rm -f /root/setup_repo.sh /root/setup_env.sh /root/.bash_history; '
                    'rm -rf /root/.cache/pip /root/.cache/huggingface')
        imported = alignment(env, case['package'])
        history = python(env, '''import json,pathlib,socket,subprocess
def git(*a): return subprocess.check_output(['git']+list(a)).decode().strip()
base=BASE
objects=git('cat-file','--batch-all-objects','--batch-check=%(objectname) %(objecttype)').splitlines()
all_objects={line.split()[0] for line in objects}
reachable=set(git('rev-list','--objects','--no-object-names','HEAD').splitlines())
commits=[line.split()[0] for line in objects if line.endswith(' commit')]
assert commits==[base] and all_objects==reachable
assert git('show-ref').splitlines()==[base+' refs/heads/baseline']
assert not git('remote') and not git('status','--porcelain')
assert not git('fsck','--full','--no-reflogs','--unreachable')
assert not pathlib.Path('.git/objects/info/alternates').exists()
logs={str(p):p.read_text() for p in pathlib.Path('.git/logs').rglob('*') if p.is_file()}
for text in logs.values():
 for line in text.splitlines(): assert set(line.split()[:2]) <= {base,'0'*40}
assert git('config','protocol.allow')=='never'
remote=subprocess.run(['git','ls-remote','https://github.com/astropy/astropy.git'],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
assert remote.returncode!=0
assert not pathlib.Path('/swe_util/eval_data').exists()
assert not pathlib.Path('/var/run/docker.sock').exists()
s=socket.socket();s.settimeout(2)
try:
 s.connect(('1.1.1.1',443)); blocked=False
except OSError: blocked=True
finally: s.close()
assert blocked
print(json.dumps({'baseline':base,'refs':git('show-ref'),'reflogs':logs,
 'commit_objects':commits,'objects':len(objects),'unreachable_objects':0,
 'remotes':[],'remote_access_exit':remote.returncode,'external_tcp_blocked':blocked}))
'''.replace('BASE', repr(case['base_commit'])))
        repos = env.checked('find / -path /proc -prune -o -path /sys -prune -o '
                            '-path /dev -prune -o -name .git -print -prune 2>/dev/null').splitlines()
        assert repos == ['/testbed/.git'], repos
        attrs = client.containers.get(env.container_id).attrs
        assert not attrs['Mounts'] and not attrs['HostConfig']['Binds']
        assert attrs['HostConfig']['NetworkMode'] == 'none'
        assert not attrs['HostConfig']['Privileged']
        probe = env.execute({'command': '(printf "intentional failure\\n" >&2; exit 7) | cat'})
        assert probe['returncode'] == 7, probe
        dependencies = env.checked('python -m pip freeze')
        return env, {'passed': True, 'model_calls': 0, 'startup': startup,
                     'alignment': imported, 'history': history, 'repositories': repos,
                     'mounts': [], 'network': 'none', 'failure_probe': probe,
                     'dependencies': dependencies}
    except BaseException:
        env.cleanup()
        raise
    finally:
        client.close()


def verify_image(case):
    import subprocess

    result = subprocess.run(['docker', 'image', 'inspect', case['image_name'],
                             '--format', '{{.Id}}'], capture_output=True, text=True, check=True)
    if result.stdout.strip() != case['image_id']:
        raise RuntimeError('Cached image tag changed since preparation: ' + case['instance_id'])


def save_dependencies(folder: Path, audit):
    (folder / 'dependencies.txt').write_text(audit.pop('dependencies'))
