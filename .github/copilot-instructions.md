# Repository Guidance

- Write explanations and documentation for groundwater modelers familiar with
  IWFM, without assuming Python or Git experience.
- Favor straightforward code and narrowly scoped changes over new abstractions.
- Preserve documented IWFM dates, source coverage, units, category identities,
  geometry denominators, and defaults unless the task explicitly changes them.
- Keep missing coverage distinct from zero and group membership ID-based.
- Check both current and historical-NAIP imagery modes when changing the shared
  template. Template-string edits also change generated viewer files.
- Keep model decks, personal paths, environments, snapshots, generated HTML, and
  comparison outputs out of commits.
- Use [CONTRIBUTING.md](../CONTRIBUTING.md) for architecture, the code map, and
  actual dependency/test commands. Do not duplicate that guidance here.