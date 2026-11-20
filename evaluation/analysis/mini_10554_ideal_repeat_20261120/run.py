"""Independent ideal-only repeat with current native protocol and fixed original Q/A."""

import argparse
import json
from pathlib import Path
import shutil

from evaluation.analysis.mini_10554_direct_proxy_20261120 import run as direct
from evaluation.analysis.mini_10554_protocol_v2_20261119 import run as native

common = direct.common
REFERENCE = direct.ARCHIVE / 'mini_swe/deepseek-10554-ideal-thinking-native-20261119-v2'


def prepare(folder):
    direct.prepare(folder)
    for name in ('initial_question.txt', 'partial_answer.txt'):
        (folder / name).unlink()
    for name in ('issue.txt', 'ideal_question.txt', 'ideal_answer.txt', 'task.txt'):
        shutil.copy2(REFERENCE / name, folder / name)
    shutil.copy2(REFERENCE / 'public_input.json', folder / 'public_input.json')
    assert (folder / 'dependencies.txt').read_bytes() == (REFERENCE / 'dependencies.txt').read_bytes()
    manifest = json.loads((folder / 'manifest.json').read_text())
    old = json.loads((REFERENCE / 'manifest.json').read_text())
    for key in ('model_settings', 'image_id', 'base_commit'):
        assert manifest[key] == old[key]
    source = Path(__file__)
    relative = source.relative_to(common.PROJECT)
    target = folder / 'frozen_source' / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    manifest['source_sha256'][str(relative)] = common.sha(source)
    manifest.update(
        condition='ideal_no_followup', comparison_reference=str(REFERENCE),
        checker_enabled=False, proxy_enabled=False, followups_enabled=False,
        max_followups=0, clarification_actor='None; fixed original ideal Q/A',
        task_content='Original saved issue and ideal Q/A, byte-identical to prior ideal run',
        experiment_change='Independent ideal repeat with current native protocol v2; no file-path suffix',
        extra_file_hint=False, extra_repair_advice=False, extra_test_context=False,
        handoff_frozen_at=common.now(),
        input_sha256={p.name: common.sha(p) for p in folder.glob('*.txt')},
    )
    common.dump(folder / 'manifest.json', manifest)
    common.dump(folder / 'clarification_result.json', {
        'status': 'not_run_fixed_original_ideal', 'elapsed_seconds': 0, 'followups_used': 0})
    common.dump(folder / 'input_verification.json', {
        name: (folder / name).read_bytes() == (REFERENCE / name).read_bytes()
        for name in ('issue.txt', 'ideal_question.txt', 'ideal_answer.txt', 'task.txt', 'dependencies.txt')})


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'run'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'run': native.run}[args.stage](args.output.resolve())
