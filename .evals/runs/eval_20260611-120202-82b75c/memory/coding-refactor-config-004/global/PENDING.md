# Pending Memory


- [decision] Configuration constants were consolidated into a SETTINGS dict instead of separate DEFAULT_TIMEOUT/RETRY_COUNT variables. (source: `task:coding-785d84b6/llm`)

- [preference] Do not modify tests.py to make the refactoring pass; only change application code. (source: `task:coding-785d84b6/llm`)

- [fact] The build_config() function returns a dictionary with keys 'timeout' and 'retries', now sourced from SETTINGS. (source: `task:coding-785d84b6/llm`)

- [fact] The test_describe_config() in tests.py asserts that describe() produces 'timeout=30 retries=3'. (source: `task:coding-785d84b6/llm`)

- [project] The project uses pytest for testing; test run command: python -m pytest tests.py -q. (source: `task:coding-785d84b6/llm`)
