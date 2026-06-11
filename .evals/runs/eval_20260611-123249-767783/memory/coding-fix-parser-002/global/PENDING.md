# Pending Memory


- [fact] parse_port("") must return 8000 as the default port, not 0. (source: `task:coding-48b17f53/llm`)

- [fact] The parse_port function is defined in src/parser.py. (source: `task:coding-48b17f53/llm`)

- [fact] Tests for parse_port are located in tests/test_parser.py using pytest. (source: `task:coding-48b17f53/llm`)

- [decision] Empty string input to parse_port is treated as request for default port, returning 8000. (source: `task:coding-48b17f53/llm`)

- [fact] The project uses pytest as the test runner with the command `python -m pytest -q`. (source: `task:coding-48b17f53/llm`)
