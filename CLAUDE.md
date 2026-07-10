# OpenSpec Initialization & Rules

We are using the OpenSpec framework to develop a Python project that analyzes audio and injects automated HotCues into Traktor Pro's `collection.nml`.

## Project Structure
- `.openspec/1-proposal.md` -> High-level goals, user stories, and constraints.
- `.openspec/2-spec.md` -> Detailed technical specifications, math equations, and data structures.
- `src/` -> Python source code.

## Agent Instructions
- **Strict Adherence:** Before writing or modifying any code, always check the state of the project in `.openspec/2-spec.md`.
- **Iterative Process:** Do not implement features that are not explicitly detailed in the specification. If you find an edge case, ask to update `2-spec.md` first.

### Test Suite Integrity & Auto-Healing Rule
- **Zero-Tolerance for Red Tests:** You are explicitly authorized and required to maintain a 100% green test suite. 
- **Proactive Auto-Healing:** If a code change modifies an architecture, message string, or logic that triggers a failure in *any* existing test (even if untouched by the core prompt), you must proactively fix the corresponding test files to align them with the new reality.
- **No Excuses:** Never drop a task leaving behind "pre-existing" or "unrelated" test failures. A feature or refactor is only considered done when the command `pytest` runs and passes completely without errors.
