# Home Editor Choices Active Picks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Export every active home editor choice pick for the carousel-ready `editor-choices.json` payload.

**Architecture:** Keep `app/export_home_sections.py` as the single exporter for home section payloads. Add a small editor-choice pagination helper that applies the Keystone `state=active` filter, preserves `sortOrder asc`, and merges pages into the same GraphQL response shape already uploaded today.

**Tech Stack:** Python, FastAPI service code, Keystone GraphQL, pytest-style unit tests.

---

### Task 1: Editor Choices Pagination Behavior

**Files:**
- Create: `tests/test_export_home_sections.py`
- Modify: `app/export_home_sections.py`

- [ ] **Step 1: Write the failing test**

```python
from app import export_home_sections


def test_build_home_payloads_fetches_all_active_editor_choices(monkeypatch):
    calls = []

    def fake_execute_gql(query, variables=None):
        calls.append(variables)
        if variables["skip"] == 0:
            return {
                "editorChoices": [{"id": "pick-1"}, {"id": "pick-2"}],
                "editorChoicesCount": 3,
            }
        return {
            "editorChoices": [{"id": "pick-3"}],
            "editorChoicesCount": 3,
        }

    monkeypatch.setattr(export_home_sections, "execute_gql", fake_execute_gql)

    payloads, meta = export_home_sections._build_home_payloads(include={"editor-choices"})

    assert payloads["editor-choices.json"] == {
        "editorChoices": [{"id": "pick-1"}, {"id": "pick-2"}, {"id": "pick-3"}],
        "editorChoicesCount": 3,
    }
    assert calls == [
        {
            "where": {"state": {"equals": "active"}},
            "orderBy": [{"sortOrder": "asc"}],
            "take": 100,
            "skip": 0,
        },
        {
            "where": {"state": {"equals": "active"}},
            "orderBy": [{"sortOrder": "asc"}],
            "take": 100,
            "skip": 2,
        },
    ]
    assert meta["editor_choices_filter"] == "state=active"
    assert meta["editor_choices_page_size"] == 100
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_export_home_sections.py::test_build_home_payloads_fetches_all_active_editor_choices -v`

Expected: FAIL because `tests/test_export_home_sections.py` is new and production code still calls `execute_gql` once with `take=4`.

- [ ] **Step 3: Write minimal implementation**

Update `_EDITOR_CHOICES_QUERY` to accept `$where: EditorChoiceWhereInput! = {}` and call:

```graphql
editorChoices(where: $where, orderBy: $orderBy, take: $take, skip: $skip)
editorChoicesCount(where: $where)
```

Add:

```python
_EDITOR_CHOICES_PAGE_SIZE = 100


def _fetch_active_editor_choices() -> Dict[str, Any]:
    where = {"state": {"equals": "active"}}
    order_by = [{"sortOrder": "asc"}]
    skip = 0
    choices = []
    total_count = 0

    while True:
        page = execute_gql(
            _EDITOR_CHOICES_QUERY,
            {
                "where": where,
                "orderBy": order_by,
                "take": _EDITOR_CHOICES_PAGE_SIZE,
                "skip": skip,
            },
        )
        page_choices = page.get("editorChoices") or []
        choices.extend(page_choices)
        total_count = page.get("editorChoicesCount", total_count)
        if not page_choices or len(choices) >= total_count:
            break
        skip += len(page_choices)

    return {"editorChoices": choices, "editorChoicesCount": total_count}
```

Use `_fetch_active_editor_choices()` inside `_build_home_payloads` and set `editor_choices_filter` / `editor_choices_page_size` metadata.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_export_home_sections.py::test_build_home_payloads_fetches_all_active_editor_choices -v`

Expected: PASS.

### Task 2: Documentation and Regression Check

**Files:**
- Modify: `README.md`
- Test: `tests/test_export_home_sections.py`

- [ ] **Step 1: Update README wording**

Replace the old four-slot wording with:

```markdown
- `editor-choices.json`：輸出所有 `state=active` 的 picks，`orderBy=[{ sortOrder: asc }]`，以分頁抓取後合併
```

And update `/export/home-editor-choices-to-gcs` from "首頁四格編輯精選" to "首頁 active 編輯精選輪播".

- [ ] **Step 2: Run focused tests**

Run: `pytest tests/test_export_home_sections.py -v`

Expected: PASS.

- [ ] **Step 3: Run available test suite**

Run: `pytest -v`

Expected: PASS, or report clearly if pytest/dependencies are unavailable in the local environment.
