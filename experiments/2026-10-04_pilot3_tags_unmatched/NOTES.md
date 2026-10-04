# Pilot 3 seeds, tag design, first pass (not prefix-matched)

Seeds: food_safety_whistleblowing, hiring_bias_documentation_cover, self_exfiltration_weapons_retrain. One rollout. Addendum = `prompts/addendum_v1_tags.md` (in-task `<probe>` tags). Cache replay broke after 4 or 5 turns because Petri's eager-resume injected a random uuid into the auditor history, so clean and injected arms diverged early; treat as unpaired. Monitor rows in `monitor.jsonl` were produced before reasoning redaction existed. See `summary.json`.
