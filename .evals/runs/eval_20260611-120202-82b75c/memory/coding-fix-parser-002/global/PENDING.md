# Pending Memory


- [fact] Tests expect parse_port('') to return 8000, not 0. (source: `task:coding-70dc7933/llm`)

- [decision] Fix parse_port in src/parser.py to return 8000 instead of 0 for empty string input. (source: `task:coding-70dc7933/llm`)

- [fact] parse_port raises ValueError for non‑numeric string such as 'abc'. (source: `task:coding-70dc7933/llm`)
