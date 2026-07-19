# Recursive://Neon reuse assessment

Recursive://Neon is an Apache-2.0 donor owned by the Drei owner. No source was copied during bootstrap.

Candidate concepts are its buffer/mark/undo behavior, symbolic key notation, in-process `EditorHarness`, structured screen snapshots, readiness predicates, field-level parity comparison, and explicit expected-divergence baselines. Product-specific game services, virtual-filesystem assumptions, and the custom PTY lifecycle are not adoption targets.

For each adoption: identify exact behavior and provenance; write and observe a failing Drei test; extract or reimplement the smallest useful component; remove game assumptions and ambient effects; record copied-file attribution in `NOTICE`; verify through direct semantics and then TermVerify. Prefer scenario intent over transport code because TermVerify owns process, readiness, constraint, teardown, and evidence contracts.
