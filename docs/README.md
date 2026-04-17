# Documentation

The root [README](../README.md) is intentionally short. Keep feature details here.

## Read In This Order

1. [features.md](features.md)
   - what this fork adds on top of upstream OpenHarness
   - why each feature exists
   - where the implementation lives

2. [architecture.md](architecture.md)
   - upstream control plane vs fork execution plane
   - YAML workflow execution
   - coordinator/swarm handoff
   - Harbor execution flow

3. [runs.md](runs.md)
   - run ID format
   - artifact layout
   - Langfuse trace identity
   - Harbor result metadata

4. [examples.md](examples.md)
   - what each example demonstrates
   - how to run each one
   - rules for adding or removing examples

## Documentation Policy

Docs should match the current branch, not aspirational architecture.

Keep:

- concise README
- feature-level docs in `docs/`
- example-specific docs in `examples/README.md`

Avoid:

- large duplicated feature lists
- stale upstream merge notes
- examples that only change a prompt or model flag
- claims that are not exercised by tests or examples
