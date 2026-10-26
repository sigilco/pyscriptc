## Summary

<!-- Brief summary of what this PR does and why. Link issue: Refs #N or Closes #N. -->

## Checklist

- [ ] Atomic conventional commit(s) without co-author trailers
- [ ] No emdashes used in markdown documentation or messages
- [ ] Markdown formatted with one paragraph per line
- [ ] No absolute home paths, personal hostnames or emails in notes
- [ ] Docs updated if API/ABI/behavior changed (see `.agents/docs/doc-cadence.md`)
- [ ] Tests added or updated for new functionality
- [ ] Targeted test run passes: `uv run pytest -n 2 <targeted_tests>`
- [ ] Lint and format checks pass: `uv run ruff check` and `uv run ruff format --check`
