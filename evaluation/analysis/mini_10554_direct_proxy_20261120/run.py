"""10554 partial-followup repeat changing only rule-template vs direct-answer proxy."""

import argparse
import json
from pathlib import Path
import shutil
import time
import tomllib

from evaluation.analysis.mini_10554_ideal_20261119 import run as common
from evaluation.analysis.mini_10554_partial_20261119 import run as original
from evaluation.analysis.mini_10554_protocol_v2_20261119 import run as native
from evaluation.utils import simple_clarification
from evaluation.utils.clarification_evidence import markdown_evidence_parts


ARCHIVE = Path('/home/rwh/Ambig-SWE-Origin/InteractiveSWEAgents/evaluation/evaluation_outputs')
common.OLD = ARCHIVE / 'model-swap-10554-offline-20261119'


def prepare(folder):
    base = ARCHIVE / 'mini_swe'
    native.prepare(folder, True, base / 'deepseek-native-v2-live-probes-20261119-v1',
                   base / 'deepseek-native-v2-offline-replays-20261119-v2')
    manifest = json.loads((folder / 'manifest.json').read_text())
    for source in (Path(__file__), common.PROJECT / 'evaluation/utils/source_grounded_proxy.py',
                   common.PROJECT / 'evaluation/utils/clarification_evidence.py',
                   common.PROJECT / 'tests/unit/test_source_grounded_proxy.py'):
        relative = source.relative_to(common.PROJECT)
        target = folder / 'frozen_source' / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest['source_sha256'][str(relative)] = common.sha(source)
    manifest.update(proxy_policy='source-grounded-reviewed-v3',
                    proxy_source_fields=['private_source.issue', 'private_source.hints'],
                    proxy_uses_rule_tables=False, proxy_uses_fact_ids=False,
                    proxy_sampling={'temperature': 0, 'top_p': 1},
                    proxy_response_format='JSON answer+source_quotes; only answer disclosed',
                    proxy_candidate_limit=2, proxy_review_model='deepseek-flash',
                    proxy_config_name='hidden_deepseek_flash',
                    all_roles_model='openai/deepseek-flash',
                    checker_evidence_matching='Exact text/whitespace, plus Markdown fence info-label compatibility only',
                    proxy_output_limit='No explicit max_tokens, matching previous proxy requests',
                    experiment_change='Direct natural answers with source citations, code validation and bounded grounding review; checker tolerates only fence-label formatting differences',
                    comparison_reference=str(base / 'deepseek-10554-partial-direct-proxy-20261120-v1'))
    manifest['clarification_settings'] = {
        'hidden_deepseek_flash': manifest['clarification_settings']['hidden_deepseek_flash']}
    common.dump(folder / 'manifest.json', manifest)


def clarify(folder):
    from openai import OpenAI
    import httpx
    from evaluation.utils.answer_check import checked_solving_instruction
    from evaluation.utils.source_grounded_proxy import SourceGroundedProxy
    from evaluation.utils.simple_clarification import SimpleJSONChecker, run_simple_clarification

    manifest = json.loads((folder / "manifest.json").read_text())
    assert not (folder / "clarification_started.json").exists(), "Preserve prior attempt"
    assert all(common.sha(common.PROJECT / p) == digest for p, digest in manifest["source_sha256"].items())
    common.dump(folder / "clarification_started.json", {"at": common.now()})
    start = time.monotonic()
    fixture_path = common.OLD / "frozen/fixture.json"
    assert common.sha(fixture_path) == manifest["old_fixture_sha256"]
    fixture = json.loads(fixture_path.read_text())
    public = json.loads((folder / "public_input.json").read_text())
    exchange = {k: public[k] for k in ("question", "answer")}
    settings = tomllib.loads((common.PROJECT / "config.toml").read_text())["llm"]
    clients = []

    def client_for(name, role):
        cfg = settings[name]
        assert {k: v for k, v in cfg.items() if "key" not in k} == manifest["clarification_settings"][name]
        wire = folder / "clarification_http" / role
        wire.mkdir(parents=True, exist_ok=False)
        counts = {"request": 0, "response": 0}

        def request(req):
            counts["request"] += 1
            common.dump(wire / f"{counts['request']:03}.request.json",
                        {"method": req.method, "url": str(req.url), "body": json.loads(req.content)})

        def response(res):
            res.read()
            counts["response"] += 1
            try:
                body = res.json()
            except ValueError:
                body = {"raw_text": res.text}
            common.dump(wire / f"{counts['response']:03}.response.json", {"status_code": res.status_code, "body": body})

        http = httpx.Client(event_hooks={"request": [request], "response": [response]})
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["base_url"], timeout=120,
                        max_retries=0, http_client=http)
        clients.append(client)
        return client

    if manifest["followups_enabled"]:
        proxy = SourceGroundedProxy(fixture, "partial", client_for("hidden_deepseek_flash", "proxy"),
                                settings["hidden_deepseek_flash"]["model"].removeprefix("openai/"),
                                folder / "interactions.json", 0, 1)
        assert proxy.initial_exchange() == exchange
        checker = SimpleJSONChecker(client_for(original.CONFIG, "checker"), settings[original.CONFIG]["model"])
        previous_evidence = simple_clarification.public_evidence_parts
        simple_clarification.public_evidence_parts = markdown_evidence_parts
        try:
            record = run_simple_clarification(checker, public["issue"], [exchange], proxy,
                                              folder / "answer_check.json", max_followups=2)
        finally:
            simple_clarification.public_evidence_parts = previous_evidence
    else:
        record = {
            "protocol": "fixed-initial-only-v1", "status": "not_run", "task_summary": public["issue"],
            "exchanges": [exchange], "checks": [], "max_followups": 0, "followups_used": 0,
            "stop_reason": "initial_only_control", "unresolved_information": "",
            "unavailable_information": [], "error": None,
        }
        common.dump(folder / "answer_check.json", record)
    for client in clients:
        client.close()
    common.dump(folder / "clarification_result.json", {"elapsed_seconds": time.monotonic() - start,
                "status": record["status"], "followups_used": record["followups_used"]})
    if record["status"] == "failed":
        raise RuntimeError("Clarification failed; no repair run: " + str(record["error"]))
    task = checked_solving_instruction(public["issue"], record)
    (folder / "task.txt").write_text(task)
    common.dump(folder / "public_handoff.json", {"issue": public["issue"], "exchanges": record["exchanges"]})
    manifest["input_sha256"]["task.txt"] = common.sha(folder / "task.txt")
    manifest["handoff_frozen_at"] = common.now()
    common.dump(folder / "manifest.json", manifest)
    print("HANDOFF FROZEN " + str(folder), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=['prepare', 'clarify', 'run'], required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    {'prepare': prepare, 'clarify': clarify, 'run': native.run}[args.stage](args.output.resolve())
