# Metric definitions

All metrics are calculated in the browser by [assets/dash-engine.js](../assets/dash-engine.js) and tested in
[tests/engine.test.js](../tests/engine.test.js). They follow the current filters and click selections.

## Period

| Term | Definition |
|---|---|
| **AS of date** | latest sales day in the data |
| **Current month (Cur)** | month of the AS of date, or the latest month picked in the Month slicer |
| **Previous month (Pre)** | the month before the current month |
| **Run-rate day** | AS of day − 1 (the AS of day itself is treated as still incomplete). For a past month picked in the slicer: all its days |
| **Weeks** | Week 1 = days 1–7, Week 2 = 8–14, Week 3 = 15–21, Week 4 = 22–end of month |

## Sales

| Metric | Formula |
|---|---|
| **GA** | gross adds — new SIM activations (sum of `ga`) |
| **M1** | revenue (sum of `m1_amount`, the file's `REV_AMT`) |
| **AP1D** | sum of `ap1d` from the sales file; also split into 8 price bands (1-49 … 300UP) |
| **GA RR (run rate)** | Cur GA × 31 ÷ run-rate day — projected full month. For a complete past month: Cur GA |
| **M1 RR / AP1D RR** | same rule as GA RR |
| **Diff GA** | GA RR − Pre GA |
| **%MoM** | (GA RR − Pre GA) ÷ Pre GA; empty when Pre GA = 0 |
| **%Thai** | GA with document type *Thai ID* ÷ Cur GA |
| **%AP1D** | AP1D ÷ GA (current and previous month) |
| **ARPU** (strategy matrix) | M1 RR ÷ GA RR |

## Weekly

| Metric | Formula |
|---|---|
| **Week GA (complete week)** | GA of the week × 7 ÷ days in the week (week 4 has 7–10 days) — every week shown as a 7-day value |
| **Current week GA** | GA so far this week × 7 ÷ days elapsed in the week |
| **%WoW** | (week − previous week) ÷ previous week; Week 1 is compared with **last month's Week 4**, normalised to 7 days |

## Targets

| Metric | Formula |
|---|---|
| **Target (Overall)** | sum of owner targets for the owners in view, for the selected channel(s) and target type(s): 7-11 and RTR owner target sheets |
| **Target (Data By Store)** | 7-11: store target; RTR: owner target of the RTR owners in view |
| **% Achieve** | GA RR ÷ target (same for M1) |
| **Gap to target** | GA RR − target |
| **Target daily** | Overall: target ÷ 31; 7-11 store target: CEILING(target ÷ 30) per store |

## Game plans

| Metric | Formula |
|---|---|
| **Plan GA** | GA of the plan's stores, counting only the plan's SIM group: plan 1 (199→350) = *Mass 350* SIMs, plan 2 (49→199) = *Mass 199* SIMs, other 7-11 plans and all RTR plans = all SIMs |
| **Commit GA** | sum of the plan's store commitments (only stores left in the current filter) |
| **Diff commit / %Commit** | Plan GA RR − Commit; ÷ Commit |
| **Commit GA daily** | sum of the daily commitments of the plans in view |
| **Close Gap commit** | 7-11 store commitment to close its gap; summed per sub-district |
| **Channel subtotal** | sum of the channel's plans (a store in two plans counts in both) |

## Market share

| Metric | Formula |
|---|---|
| **Market share (True)** | average of the store-level True share of the **RTR** stores in view; 7-11 stores count as 100% |
| **Market size** | sum of market size of the RTR stores in view |

## Store alert

For each 7-11 zone, using every store with sales last month:

1. weight = store's previous-month GA
2. zone average = weighted mean of %MoM
3. zone spread = weighted standard deviation of %MoM
4. **alert** when the store's %MoM ≤ zone average − zone spread

The alert shows on Data By Store when one store is selected.
