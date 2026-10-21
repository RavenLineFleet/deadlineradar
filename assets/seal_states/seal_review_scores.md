# State-by-state seal review (alphabetical)

STANDING RULE (Devin, 2026-10-20): once a state PASSes, its exact box is locked in
`seal_locked_boxes.json` and never recomputed again, even by a later global formula change made
to fix some other state. Only touch a passed state again if Devin explicitly says to.

| State | Verdict | Notes |
|---|---|---|
| AK | PASS | |
| AL | PASS (after fix) | date text had been illegible (narrow-state date-width bug); fixed globally via decoupled fixed-width date plate; re-checked and locked |
| AR | PASS (after fix) | had read too large; passes clean under current MAXW=52/MAXH=39; re-checked and locked |
| AZ | PASS (after fix) | was touching arc text / too big; fixed by trimming MAXH 41->39 (date position kept fixed, independent of MAXH) |
| CA | PASS | |
| CO | PASS (after fix) | was too large, then needed re-centering; locked |
| CT | PASS | locked |
| DC | PASS | locked |
| DE | PASS | locked |
| FL | PASS (after fix) | panhandle touching arc text; fixed via more internal padding on FL's own enamel PNG (pad_frac 0.08->0.16); locked |
| GA | PASS | locked |
| HI | PASS | locked |
| IA | PASS | locked |
| ID | PASS | locked |
| IL | PASS | locked |
| IN | PASS | locked |
| KS | PASS (after fix) | too big (MINH floor 26->24 for all future floor-triggered states); locked |
| KY | PASS | locked |
| MA | PASS | locked |
| MD | PASS | locked |
| ME | PASS | locked |
| MI | PASS | locked |
| LA | PASS (after fix) | too big, fixed via own pad_frac bump (0.08->0.14); locked |
| MN | PASS | locked |
| MO | PASS | locked |
| MS | PASS | locked |
| MT | PASS | locked |
| NC | PASS (after fix) | too big, pad_frac bump; locked |
| ND | PASS | locked |
| NE | PASS (after fix) | too big, pad_frac bump; locked |
| NH | PASS | locked |
| NJ | PASS | locked |
| NM | PASS | locked |
| NV | PASS | locked |
| NY | PASS | locked |
| OH | PASS (after fix) | was touching arc text ("E"); fixed via own pad_frac bump (0.08->0.15); locked |
| OK | PASS | locked |
| OR | PASS | locked |
| PA | PASS | locked |
| RI | PASS | locked |
| SC | PASS | locked |
| SD | PASS | locked |
| TN | PASS (after fix) | too big; fixed via own pad_frac bump (0.08->0.18, shrinks visible shape w/o changing box); locked |
| UT | PASS | locked |
| VA | PASS | locked |
| VT | PASS | caught as a gap after the full sweep -- had been skipped between UT and VA, never built or shown until caught; locked |
| WA | PASS | locked |
| WI | PASS | locked |
| WV | PASS | locked |
| WY | PASS | locked |
| TX | PASS | locked -- control state, first formal verdict |

## Territories (added 2026-10-20, scope expansion per Devin)

| Territory | Verdict | Notes |
|---|---|---|
| PR | PASS | Puerto Rico, world-atlas (Natural Earth) source since us-atlas has no territories; locked |
| GU | PASS | Guam; locked |
| VI | PASS (after fix) | was too large; archipelago -- main-island-only extraction (same AK-mainland technique) + pad_frac bump (0.08->0.35); locked |
| AS | PASS | American Samoa; locked |
| MP | PASS | Northern Mariana Islands; archipelago -- main-island-only extraction (same AK-mainland technique); locked |
