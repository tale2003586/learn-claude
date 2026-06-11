# Pending Memory


- [fact] clamp(value, low, high) must return low when value < low and high when value > high. Previous implementation returned the opposite, breaking boundary tests. (source: `task:coding-040d9df2/llm`)

- [project] Tests for clamp are in tests/test_math_tools.py and cover below range, above range, and inside range. These tests define the expected semantics. (source: `task:coding-040d9df2/llm`)

- [decision] When fixing implementation bugs, only modify source files; do not alter test files. (source: `task:coding-040d9df2/llm`)
