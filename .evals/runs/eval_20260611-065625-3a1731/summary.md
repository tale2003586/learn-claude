# Coding Agent Benchmark

## Summary

- Eval ID: `eval_20260611-065625-3a1731`
- Runner: `real` / `deepseek-v4-pro`
- Benchmark: `benchmarks/coding_tasks.json`
- Task filter: `coding-invalid-edit-recovery-009`
- Workspace retained: `True`
- Workspace root: `/tmp/coding-benchmark-jrs8qkhw`

- Tasks: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.00%
- Verifier pass rate: 0.00%
- Workspace diff pass rate: 0.00%
- Trace completeness rate: 0.00%
- Avg reasoning steps: 30.00
- Avg tool calls: 55.00

## Failure Categories

- `verifier_failed`: 1

## Rows

| id | category | status | failure | reason | steps | tools |
| --- | --- | --- | --- | --- | ---: | ---: |
| coding-invalid-edit-recovery-009 | recovery | fail | verifier_failed | budget_stop | 30 | 55 |

## Failed Tasks

### coding-invalid-edit-recovery-009

- Failure: `verifier_failed`
- Reason: `budget_stop`
- Run dir: `.evals/runs/eval_20260611-065625-3a1731/runs/run_20260611-065625-c7f75a`
- Workspace: `/tmp/coding-benchmark-jrs8qkhw/coding-invalid-edit-recovery-009/coding_invalid_edit`

- failed `modified` notes.txt
- failed `file_contains` notes.txt
- failed `tool_called` edit_file

