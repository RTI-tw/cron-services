# Home Editor Choices Active Picks Design

## Goal

Change the home `editor-choices.json` export from the legacy four-slot payload to a carousel-ready payload containing every active editor choice pick.

## Scope

- Applies to both `/export/home-sections-to-gcs` and `/export/home-editor-choices-to-gcs`.
- Keeps the existing output file name, top-level GraphQL response shape, and editor choice fields.
- Leaves `footer.json` and `pop-polls.json` behavior unchanged.

## Query Behavior

The editor choices query will filter Keystone `EditorChoice` records with:

```json
{ "state": { "equals": "active" } }
```

Results remain ordered by `sortOrder asc`.

The exporter will fetch editor choices in pages and merge them before upload. This avoids relying on Keystone's implicit list limits and makes "all active picks" explicit.

## Output Behavior

`editor-choices.json` will still contain:

- `editorChoices`: the merged list of active picks.
- `editorChoicesCount`: the active pick count returned by Keystone.

The export metadata will no longer report `editor_choices_take=4`. It will report that active picks were exported and include the configured page size.

## Tests

Add focused unit tests for:

- The editor choices export uses `where state equals active`.
- Multiple pages of active editor choices are merged into one payload.
- Existing non-editor home section behavior is not changed by this helper.
