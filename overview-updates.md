# Overview Repo Updates — content_type System

Changes to apply to `AlpheusCEF/overview` once this PR is merged.

## `STATE.md`

### 1. Optional fields table (after the required fields table)

Add `content_type` as the first optional field row:

```markdown
| `content_type` | Content format: `text` (default/implicit), `gdoc`, `slack`, `jira`, `confluence`, `email`, `image`, `figma` — determines expected `meta` fields |
```

### 2. New section — "Content Types" (after Node Status section)

```markdown
### Content Types

`content_type` identifies **what kind of content** the node represents — distinct from `source` (who created it) and `node_type` (snapshot vs. live). Omitting `content_type` is equivalent to `content_type: text`.

| Value | Required `meta` fields | Notes |
|-------|------------------------|-------|
| `text` (default) | _(none)_ | Plain text, manual input |
| `gdoc` | `url` | Google Doc |
| `slack` | `url` **or** (`channel` + `thread_ts`) | Slack message or thread |
| `jira` | `url`, `issue_key` | Jira issue |
| `confluence` | `url` | Confluence page |
| `email` | `from`, `subject` | Email thread or message |
| `image` | `url` | Photo, screenshot, diagram |
| `figma` | `url` | Figma file, frame, or component |

The validator enforces required meta fields when `content_type` is present and not `text`. An unrecognised `content_type` value (e.g. `cli`, `google_doc`) causes validation to fail with a message listing valid values.

The three structural fields are orthogonal:
```
source       = WHO made the node (adapter identity)
node_type    = HOW content is stored/fetched (snapshot vs live)
content_type = WHAT the content is (text, gdoc, slack, jira...)
```
```

### 3. Decisions Made list

Add after the `status` bullet:

```markdown
- `content_type` field (`text`, `gdoc`, `slack`, `jira`, `confluence`, `email`, `image`, `figma`) identifies content format; optional, defaults to `text`; validator enforces required `meta` fields per type; `--content-type`/`--ct` flag on `alph add`; shown as a column in `alph list` and field in `alph show`
```

## `plans/content-type-node-system.md`

Update the status line from:

```
_Status: Design proposal — not yet implemented_
```

to:

```
_Status: Implemented in alph-cli (PR: AlpheusCEF/alph-cli branch claude/implement-content-type-nodes-OjpLY)_
```
