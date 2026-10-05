# Project context for Codex

This project is an existing, unfinished Cost Index / fuel-performance optimizer for flight simulation and real-world-inspired EFB workflows.

Before making architectural suggestions, read:

- `docs/research/pace-fpo-icelandair.md`
- existing source code
- existing tests
- README / docs if available

Important principle:

This app should not become only a "CI calculator". It should become a remaining-flight optimizer that outputs:
- recommended CI
- recommended speed/Mach
- recommended cruise flight level
- step climb/descent recommendations
- fuel estimate
- time estimate
- net cost estimate
- explanation of why the recommendation was selected

Do not claim to reproduce Pace FPO's proprietary algorithm. Use it only as product and architecture inspiration.

When reviewing the code, identify:
- what already exists
- what partially exists
- what is missing
- what is incorrectly modeled
- what should be refactored
- what should be implemented next

Prefer small, testable improvements over large rewrites.