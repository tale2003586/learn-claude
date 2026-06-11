# Pending Memory


- [fact] app/config.py now centralizes timeout and retry values in a SETTINGS dictionary with keys "timeout" and "retries". (source: `task:coding-d90a006b/llm`)

- [fact] build_config() preserves the public config shape: {"timeout": 30, "retries": 3}. (source: `task:coding-d90a006b/llm`)

- [project] The project has a root-level tests.py test file; run it explicitly with `pytest -q tests.py`. (source: `task:coding-d90a006b/llm`)
