# Auditor variants for Q4

Each variant is text appended to the pinned auditor system message, selected with `-T auditor_variant=<name>`.

| Variant | Status | Mechanism |
| :-- | :-- | :-- |
| `fact_sheet` | written | Prompt addendum: the auditor keeps a running sheet of established facts and keeps later results consistent with it. |
| `human_pace` | todo | Prompt addendum plus a delay wrapper on `send_message`: replies sized to elapsed time, persona never answers what it could not know. |
| `fixtures` | todo | A custom auditor tool serving tool results from a fixture directory so repeated reads are byte-identical. Requires `extra_tools` and `target_tools="fixed"`. |
