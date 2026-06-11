# 2026-06-12 Agent Evaluation And Framework Plan

## Objective

Tomorrow's work should validate the current coding benchmark system as an agent architecture, not only as a patch generator. The run should answer three questions:

1. Can the system reliably solve at least 12 coding tasks, including several complex tasks?
2. Can the main-agent/sub-agent protocol make complex work more inspectable and more reliable than a single-agent baseline?
3. Can the framework produce readable traces, explicit workspace boundaries, and actionable failure reports?

The expected output is a tightened benchmark loop: task definitions, run artifacts, readable trace summaries, and a clear list of framework changes that should be kept.

## Scope

The work covers four areas:

- Task execution: run at least 12 benchmark tasks.
- Multi-agent protocol: define how the main agent assigns work and how sub-agents report back.
- Trace and reporting: make run traces easier to audit without reading raw JSONL line by line.
- Workspace discipline: make the active task workspace unambiguous to the model, tools, trace, and final reports.

This plan intentionally avoids hour-by-hour scheduling. Treat each section as a completion checklist.

## Task Mix

Run at least 12 tasks.

Suggested distribution:

- 5 simple bugfix tasks.
- 3 medium tasks involving multiple files or stricter diff constraints.
- 1 real SWE-bench Lite task.
- 3 complex multi-agent tasks designed to show main-agent/sub-agent orchestration.

The complex tasks should not merely be larger. They should require useful division of labor.

### Simple Tasks

Use these to check basic agent stability:

- Boundary-condition bugfix.
- Parser edge-case bugfix.
- Add a small utility function with tests.
- Invalid edit recovery.
- Workspace-scope safety.

Required success criteria:

- Tests pass.
- Only expected files are changed.
- No shell command escapes the task workspace.
- The final answer mentions the changed files and verification result.

### Medium Tasks

Use these to check more realistic coding behavior:

- Cross-file behavior fix with one implementation file and one test file.
- Git diff discipline task where only implementation changes are allowed.
- Context/memory task where the agent must use previously discovered facts without re-reading everything.

Required success criteria:

- The agent identifies the target file before editing.
- The agent verifies the final diff.
- The trace shows a bounded inspect -> edit -> verify loop.
- Repeated failed commands or repeated identical tool calls are avoided or recovered from.

### Complex Multi-Agent Tasks

Use these to demonstrate the architecture.

Task A: multi-agent diagnosis and patch

- Main agent receives a failing project with several plausible bug locations.
- Sub-agent 1 inspects failing tests and failure messages.
- Sub-agent 2 maps relevant implementation files.
- Sub-agent 3 proposes a minimal fix and risks.
- Main agent decides the final patch and applies it.

Task B: multi-agent code review and repair

- Start from an intentionally flawed patch or a hidden regression.
- Sub-agent 1 checks correctness.
- Sub-agent 2 checks test coverage and regression risk.
- Sub-agent 3 checks diff scope and workspace hygiene.
- Main agent resolves conflicting findings and produces the final patch.

Task C: SWE-bench style issue

- Main agent reads the problem statement and assigns sub-tasks for code search, root-cause analysis, and patch validation.
- Sub-agents return evidence with file paths and reasoning.
- Main agent generates the final patch/prediction.
- Official harness result should be recorded as resolved, unresolved, or infra error.

Required success criteria:

- The trace shows task delegation, sub-agent outputs, and main-agent synthesis.
- Sub-agents produce non-overlapping evidence.
- Main agent uses sub-agent findings rather than ignoring them.
- Final patch remains minimal.
- Failure reports distinguish patch failure from infrastructure failure.

## Main-Agent/Sub-Agent Protocol

Add or formalize a small protocol for task handoff and result collection.

### Assignment Schema

Each sub-agent assignment should include:

```json
{
  "assignment_id": "short-stable-id",
  "role": "diagnosis | search | review | verification | risk",
  "objective": "one concrete task",
  "workspace_root": "absolute or canonical workspace path",
  "allowed_paths": ["path/prefix"],
  "forbidden_paths": ["path/prefix"],
  "inputs": {
    "task_request": "short summary",
    "known_files": [],
    "known_failures": []
  },
  "expected_output": {
    "summary": "required",
    "evidence": "required",
    "recommendation": "optional",
    "confidence": "required"
  },
  "stop_condition": "what counts as enough"
}
```

### Result Schema

Each sub-agent should return:

```json
{
  "assignment_id": "same id",
  "status": "completed | blocked | inconclusive",
  "summary": "short conclusion",
  "evidence": [
    {
      "file": "relative/path.py",
      "line": 12,
      "claim": "what this evidence supports"
    }
  ],
  "recommendation": "specific next action",
  "risks": ["known risk or uncertainty"],
  "confidence": "low | medium | high"
}
```

### Main-Agent Responsibilities

The main agent should:

- Create bounded assignments.
- Avoid sending every sub-agent to inspect the whole repo.
- Merge findings into a decision table.
- Resolve conflicts explicitly.
- Apply the final patch itself or through a single controlled editing step.
- Record why it accepted or rejected each sub-agent recommendation.

### Protocol Metrics

Measure:

- Number of sub-agent assignments per complex task.
- Percentage of sub-agent outputs used in the final decision.
- Duplicate-work rate: two sub-agents reporting the same file and same finding.
- Conflict-resolution quality: whether the main agent explains conflicting recommendations.
- Patch minimality: number of files touched compared with expected files.

## Trace Readability Improvements

Raw `trace.jsonl` is too detailed for routine analysis. Add a compact trace summary artifact for every run.

Recommended new artifact:

```text
trace_summary.md
```

Recommended sections:

- Run metadata: task id, run id, model, workspace root, status.
- Outcome: pass, fail, infra error, loop guard, empty response, timeout.
- Workspace: requested workspace, resolved workspace, allowed root.
- Tool timeline: only meaningful tool calls, grouped by reasoning step.
- File access summary: read files, edited files, denied paths.
- Agent delegation summary: assignments, sub-agent results, main-agent decisions.
- Verification summary: tests run, command status, final diff status.
- Failure diagnosis: primary failure reason and supporting evidence.

Recommended machine-readable artifact:

```text
trace_summary.json
```

Minimum fields:

```json
{
  "run_id": "",
  "task_id": "",
  "status": "",
  "failure_category": "none | patch_wrong | infra_error | tool_error | protocol_error | model_empty | loop_guard | timeout",
  "workspace_root": "",
  "tools": {
    "total_calls": 0,
    "denied_calls": 0,
    "repeated_calls": 0
  },
  "files": {
    "read": [],
    "modified": [],
    "unexpected_modified": []
  },
  "verification": {
    "commands": [],
    "passed": false
  },
  "multi_agent": {
    "assignments": 0,
    "completed_assignments": 0,
    "used_findings": 0
  }
}
```

Trace summary acceptance criteria:

- A failed run can be diagnosed in under 3 minutes from `trace_summary.md`.
- The summary identifies whether failure came from the model, tool/runtime, verifier, Docker/SWE-bench infrastructure, or task design.
- The summary links to raw trace and final diff for deeper inspection.

## Workspace Clarity

The workspace must be visible and enforced at every layer.

### Model Context

The task prompt or runtime context should explicitly include:

- Current workspace root.
- Allowed root.
- Whether paths should be relative to workspace.
- The exact command style expected for tests.

Example wording:

```text
Workspace root: /tmp/.../task_workspace
All file paths and shell commands must operate inside this workspace.
Use relative paths unless an absolute path is explicitly provided by the workspace resolver.
Do not cd to the benchmark repository root.
```

### Tool Enforcement

Keep enforcing:

- `list_files`, `read_file`, and `edit_file` cannot escape workspace.
- Shell commands that `cd` to an absolute path outside workspace are denied.
- Denied calls must be recorded in trace summaries.

### Reporting

Every report should include:

- `workspace_requested`
- `workspace_resolved`
- `workspace_allowed_root`
- `workspace_escape_attempts`
- `commands_with_external_cd`

Workspace success criteria:

- Zero successful shell commands run outside the task workspace.
- Any attempted external `cd` is denied and classified as workspace violation.
- The model recovers from a workspace denial by using `.` or the resolved workspace path.

## Ablation Experiments

Add a small ablation design so the evaluation shows why the architecture matters.

### Ablation A: Single Agent vs Multi-Agent

Run the same complex task in two modes:

- Single-agent mode: one agent solves end to end.
- Multi-agent mode: main agent delegates diagnosis/search/review.

Compare:

- Pass rate.
- Reasoning steps.
- Tool calls.
- Duplicate file reads.
- Patch size.
- Time to first correct target file.
- Final failure category, if any.

Expected advantage:

- Multi-agent should produce clearer evidence and fewer missed risks on complex tasks.

### Ablation B: With Protocol vs Free-Form Delegation

Run one complex task with structured assignment/result schemas and one with free-form sub-agent instructions.

Compare:

- Whether sub-agent output is directly usable.
- Duplicate-work rate.
- Main-agent synthesis quality.
- Trace readability score.

Expected advantage:

- Structured protocol should make trace summaries easier to generate and reduce vague sub-agent outputs.

### Ablation C: With Trace Summary vs Raw Trace Only

For several completed and failed runs, compare diagnosis effort:

- Raw `trace.jsonl` only.
- `trace_summary.md` plus raw trace fallback.

Compare:

- Time to identify root cause.
- Correctness of failure classification.
- Whether a human can tell what changed and why.

Expected advantage:

- Trace summary should reduce diagnosis time and avoid confusing patch failures with infra failures.

### Ablation D: Workspace Guard On vs Off

Use a task where the model is likely to run commands from the repository root.

Compare:

- Guard enabled.
- Guard disabled or report-only.

Compare:

- Incorrect test commands.
- Escaped paths.
- False failures caused by running the wrong test suite.

Expected advantage:

- Workspace guard should reduce invalid verification and misleading failures.

## Core Metrics

Collect these for every task:

```text
task_id
run_id
mode: single-agent | multi-agent
task_type: simple | medium | complex | swebench
final_status: pass | fail | infra_error
failure_category
reasoning_steps
tool_calls
denied_tool_calls
empty_model_responses
loop_guard_events
workspace_escape_attempts
files_read_count
files_modified_count
unexpected_files_modified_count
tests_run_count
verification_passed
patch_bytes
duration_seconds
```

Collect these for multi-agent tasks:

```text
assignments_created
assignments_completed
assignments_blocked
subagent_findings_total
subagent_findings_used
duplicate_findings
conflicting_findings
conflicts_resolved
main_agent_decision_recorded
```

Collect these for trace readability:

```text
trace_summary_exists
trace_summary_has_workspace
trace_summary_has_failure_category
trace_summary_has_file_summary
trace_summary_has_tool_timeline
diagnosis_time_minutes
```

## Success Targets

Minimum targets:

- At least 12 tasks run.
- At least 9 tasks complete without infrastructure errors.
- At least 3 complex tasks run in multi-agent mode.
- At least 2 ablation comparisons completed.
- 100% of runs produce `trace_summary.md` or equivalent summary artifact.
- 100% of runs record workspace root and allowed root.
- Zero successful writes outside allowed workspace.

Strong targets:

- At least 10 of 12 tasks pass or are correctly classified as infra errors.
- Complex multi-agent tasks show lower diagnosis ambiguity than single-agent baselines.
- Trace summaries allow failure classification in under 3 minutes per run.
- Workspace mistakes are denied and recovered from, not allowed to poison verification.

## Framework Changes To Consider

Prioritize small changes that make runs easier to trust.

1. Add `trace_summary.md` and `trace_summary.json` generation after every run.
2. Add explicit workspace metadata to model context and final run reports.
3. Add a multi-agent assignment/result schema.
4. Add a main-agent synthesis record that explains which sub-agent findings were used.
5. Add failure categories to run reports.
6. Add metrics extraction for ablation comparisons.
7. Add a compact eval dashboard row per task.

Suggested failure categories:

```text
pass
patch_wrong
test_failed
infra_error
docker_error
dependency_error
workspace_violation
tool_denied
loop_guard
empty_model_response
timeout
task_design_error
verifier_error
protocol_error
```

## Deliverables

Tomorrow's concrete deliverables:

- A 12-task run set with result artifacts.
- At least 3 complex multi-agent traces.
- At least 2 ablation comparisons.
- A first version of trace summary generation.
- A documented main-agent/sub-agent protocol.
- A workspace clarity patch or prompt/runtime change.
- A final report classifying each failure and listing next framework fixes.

## Final Review Questions

Use these questions to decide whether the day succeeded:

- Can I tell from the summary why each task passed or failed?
- Can I distinguish patch failure from Docker or dependency failure?
- Can I see where the workspace was resolved and enforced?
- Did sub-agents produce evidence the main agent actually used?
- Did multi-agent mode improve reliability, diagnosis clarity, or patch quality?
- Are the remaining failures tied to concrete system changes?
