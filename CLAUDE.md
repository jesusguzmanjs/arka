# OpenSpec Initialization & Rules

We are using the OpenSpec framework to develop a Python project that analyzes audio and injects automated HotCues into Traktor Pro's `collection.nml`.

## Project Structure
- `.openspec/1-proposal.md` -> High-level goals, user stories, and constraints.
- `.openspec/2-spec.md` -> Detailed technical specifications, math equations, and data structures.
- `src/` -> Python source code.

## Agent Instructions
- **Strict Adherence:** Before writing or modifying any code, always check the state of the project in `.openspec/2-spec.md`.
- **Iterative Process:** Do not implement features that are not explicitly detailed in the specification. If you find an edge case, ask to update `2-spec.md` first.
