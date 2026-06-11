# Pending Memory


- [project] Source code lives in src/ and tests in tests/; test file mirrors source file name with test_ prefix. (source: `task:coding-5fafe594/llm`)

- [decision] The clamp function must return low when value < low, high when value > high, otherwise value. (source: `task:coding-5fafe594/llm`)

- [fact] The original clamp implementation returned high for value < low and low for value > high. (source: `task:coding-5fafe594/llm`)

- [fact] Clamp tests cover three scenarios: value below range, above range, and inside range. (source: `task:coding-5fafe594/llm`)

- [preference] Bugfixes should only alter implementation files, never the tests themselves. (source: `task:coding-5fafe594/llm`)

- [fact] pytest is run via `python -m pytest -q` and the full clamp test suite executes in ~0.02s. (source: `task:coding-5fafe594/llm`)
