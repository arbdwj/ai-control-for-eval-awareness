# Clock variant: 45 timing-cited seeds, clean + injected, GLM 5.3

Question: if the auditor has a scenario clock it must set, advance before every send, and stamp every timestamp from, with an approver rejecting timestamps ahead of the clock, missing advances and repeated increments, does the timing giveaway go away, and does the target's belief move?

Seeds: the 45 whose baseline debrief (2026-10-04 full run) cited clocks, timestamps or instant responses (`seeds.txt`, `seed_selection.json`). Fresh clean audits under the clock auditor (`prompts/variants/clock.md`, tools and approver in `probing_control/clock.py`), then injected continuations at break turns drawn from the new clean runs, debriefs, three monitors. Pipelined; 19:36 to 20:57 on 2026-10-06. 42 seeds completed both arms; 38 debriefs parsed.

| Measure | Baseline | Clock |
| :-- | --: | --: |
| debriefs citing timing as evidence | 36 of 42 (86%) | 20 of 38 (53%) |
| sim_score mean | 0.81 | 0.75 |
| sim_score median | 0.85 | 0.85 |
| clock advances per audit | - | 16.6, median 28 s, 10th to 90th percentile 4 s to 9 min |
| approver rejections per audit | - | 1.5 |
| repeated consecutive increments | - | 0 |
| monitor trace scores | unchanged across arms | |

Reading: the auditor used the clock as intended and the timing giveaway fell from 86 to 53 percent of debriefs. Belief fell 6 points in the mean and not at all in the median. Where the target checked the clock it found it coherent (day of week correct, stamps agreeing to the second), and then cited the next seam: instant, complete, tidy responses; uniform mtimes; a perfectly regular 60-per-hour cadence over 239 hours; a 10-day clock jump inside one session; a `20277-02-24` timestamp. A clock makes time consistent; it does not make the world busy, irregular or imperfect. Full comparison in `comparison.md`.
