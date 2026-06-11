# Coding Agent Benchmark

## Summary

- Eval ID: `eval_20260611-064123-4b54e8`
- Runner: `real` / `deepseek-v4-pro`
- Benchmark: `benchmarks/coding_tasks.json`
- Task filter: `coding-invalid-edit-recovery-009`
- Workspace retained: `True`
- Workspace root: `/tmp/coding-benchmark-viexxauf`

- Tasks: 1
- Passed: 0
- Failed: 1
- Pass rate: 0.00%
- Verifier pass rate: 0.00%
- Workspace diff pass rate: 0.00%
- Trace completeness rate: 0.00%
- Avg reasoning steps: 7.00
- Avg tool calls: 7.00

## Failure Categories

- `verifier_failed`: 1

## Rows

| id | category | status | failure | reason | steps | tools |
| --- | --- | --- | --- | --- | ---: | ---: |
| coding-invalid-edit-recovery-009 | recovery | fail | verifier_failed | budget_stop | 7 | 7 |

## Failed Tasks

### coding-invalid-edit-recovery-009

- Failure: `verifier_failed`
- Reason: `budget_stop`
- Run dir: `.evals/runs/eval_20260611-064123-4b54e8/runs/run_20260611-064123-4ec1b2`
- Workspace: `/tmp/coding-benchmark-viexxauf/coding-invalid-edit-recovery-009/coding_invalid_edit`

- failed `modified` notes.txt
- failed `file_contains` notes.txt
- failed `tool_called` edit_file

