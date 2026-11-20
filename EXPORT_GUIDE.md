# Ambig-SWE + mini-SWE-agent export

This is an independent snapshot of the current Ambig-SWE working tree, including untracked migration code and the native-tool protocol fixes. Original project history and remotes were not modified. `EXPORT_PROVENANCE.json` records source versions and original file hashes. Original upstream licenses remain included; mini-SWE-agent 2.4.6 is vendored in `vendor/mini-swe-agent` without its Git metadata.

## Setup

Use Python 3.11 or later:

```bash
python3.11 -m venv .venv-mini
.venv-mini/bin/python -m pip install -r requirements-mini-export.txt
cp config.mini.example.toml config.toml
# Edit config.toml locally to add API keys.
bash run_ambigswe.sh --llm-config hidden_deepseek_flash --check-only --instance-ids django__django-10554
```

`MINISWE_PYTHON` overrides the interpreter. Benchmark execution requires Docker and the expected cached images. The generic launcher currently uses the text Bash interface; the native protocol experiments use the separate `evaluation/analysis/mini_10554_*` harnesses. Exporting the source does not change that behavior.

The inherited `AMBIG_SWE.md` documents the original local installation and may mention old absolute paths. Experiment analysis scripts also retain historical absolute paths and depend on saved experiment artifacts. Do not treat them as portable one-command reproductions without supplying those artifacts and updating paths.

## Included and excluded

Current tracked sources and untracked code/scripts are included. Local `config.toml`, API keys, virtual environments, raw experiment outputs, gold patches, hidden tests, and original `.git` directories are excluded. Existing benchmark datasets already tracked in the source repository remain included. All successful and failed experiment outputs remain in the original local directory.

## Checks

```bash
MSWEA_SILENT_STARTUP=1 .venv-mini/bin/python -m pytest tests/unit/test_mini_swe.py tests/unit/test_mini_swe_native.py -q
```

Original OpenHands code remains part of this snapshot so its benchmark and shared utilities remain available. It is not the mini execution framework.
