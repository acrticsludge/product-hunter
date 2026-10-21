---
name: frequency-analysis
description: Use when a count, a rate, or a trend is about to be reported, or when a mention count is being read as a people count.
---

# Frequency analysis

Frequency counts independent occurrences. It never counts mentions, and the denominator is always stated next to the number.

This skill implements §16. The DuckDB path is named in `opencode.json`, and the counting lands in Phase 3 with the analytical work in Phase 4.

## The three numbers stay apart

| Number | What it counts |
|---|---|
| mentions | every visible copy, propagation included |
| canonical observations | distinct content after propagation is folded |
| independent people | authors resolved as distinct, uncertainty reported beside them |

Never present a mention count as a people count, and never let the largest of the three stand in for the others.

## A count carries its window

A count without a window is not a number. The window, the collection period, and the sources that ran travel with the count, so the same number can be read again later.

## Concentration

A count dominated by one community is a property of that community, not a market rate. The concentration number is reported next to the count, and a report that omits it overstates the market.

## Growth needs a baseline

A first observation is not a trend, and a run that stopped early is not a decline. A trend names the two comparable windows it compares.

## DuckDB computes, the fixtures reconcile

The analytics layer in DuckDB computes the counts and trends. A DuckDB count that disagrees with the SQLite records is a defect to fix, not a rounding difference to explain away. SQLite remains the operational source of truth.

## Coverage is part of the number

A report states which query families and which sources ran, so a low count and a zero can be read correctly. A zero from an unsearched family is not a zero.

## Fail closed

No frequency claim is made without its window, its denominator, and its coverage. Never claim a market rate from a sample drawn out of one community.
