# Coding Agent Benchmark

## Summary

- Eval ID: `eval_20260611-122902-4a09de`
- Runner: `real` / `deepseek-v4-pro`
- Benchmark: `benchmarks/coding_tasks.json`
- Task filter: `coding-fix-math-001`
- Workspace retained: `True`
- Workspace root: `/tmp/coding-benchmark-e41kyi4u`

- Tasks: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.00%
- Verifier pass rate: 0.00%
- Workspace diff pass rate: 100.00%
- Trace completeness rate: 0.00%
- Avg reasoning steps: 6.00
- Avg tool calls: 6.00

## Failure Categories

- `verifier_failed`: 1

## Rows

| id | category | status | failure | reason | steps | tools |
| --- | --- | --- | --- | --- | ---: | ---: |
| coding-fix-math-001 | bugfix | fail | verifier_failed | expected_tool_not_called | 6 | 6 |

## Failed Tasks

### coding-fix-math-001

- Failure: `verifier_failed`
- Reason: `expected_tool_not_called`
- Run dir: `.evals/runs/eval_20260611-122902-4a09de/runs/run_20260611-122902-6aa86a`
- Workspace: `/tmp/coding-benchmark-e41kyi4u/coding-fix-math-001/coding_fix_math`

- failed `tool_called` git_status

