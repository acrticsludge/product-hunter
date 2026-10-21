---
name: pain-point-normalization
description: Use when turning a model's wording into a groupable key, or when comparing normalized fields across records.
---

# Pain point normalization

## The controlled fields

A genuine candidate carries five fields, and each one is a short phrase rather than a sentence:

| Field | Cap | Content |
|---|---|---|
| `domain` | fixed vocabulary | the area of work |
| `actor` | 120 | the person or team doing the work |
| `action` | 120 | a short verb phrase for what they do |
| `object` | 120 | a short noun phrase for what they work on |
| `consequence` | 200 | the concrete cost they pay |

A `weak` candidate may leave them out, and a `not_pain` candidate sets them to null. A genuine candidate missing any of them fails schema validation, and a genuine candidate whose fields cannot be keyed is rejected with `normalization_invalid` rather than stored without a key.

## The domain vocabulary

Fifteen values, fixed: `software`, `data`, `operations`, `finance`, `sales`, `marketing`, `support`, `hr`, `legal`, `security`, `research`, `education`, `healthcare`, `ecommerce`, `other`. The list does not grow per run. A new area of work maps to `other` until the vocabulary changes on purpose, and such a change means a new normalization version.

## Slugging

`slugify` normalizes NFKC, lowercases, strips accents, and replaces every run of characters that is not a lowercase ASCII letter or a digit with one dash. Leading and trailing dashes go. A slug longer than 48 characters is cut at the limit with the trailing dash removed. A phrase with nothing left to slug raises `normalization_invalid`.

The 48-character cap is a collision risk. Two different phrases whose slugs share their first 48 characters slug to the same text, so the key groups them together. A long action or object phrase has to be short enough for its meaning to survive the cut.

## The key

`normalized_key` is `domain:slug(action):slug(object)`, at most 200 characters. `actor` and `consequence` stay out of it on purpose: their wording varies too much to key on.

Every record carries `normalization_version`, currently `norm-v1`. A change to the slug rules changes the meaning of a key, so keys are only ever compared within one version and an old key is never read as a new one.

## pain_id stays span-based

`pain_id` is `pp_` plus the first 16 hex characters of `sha256(span_id + "|" + extractor_version)`. It comes from the span, not from the model's wording, so a paraphrase never forks the record and a replay reproduces the same id. Normalization adds a grouping key; it leaves the identity of a record alone.

## The key groups exact wording only

Two sources that describe the same problem in different words produce different keys. That is expected. The key supports exact grouping, not semantic clustering, and no claim of semantic clustering rests on it. It is a grouping, not a unique identifier: the 48-character slug cap lets two different long phrases share a key. Model wording drift shows up as several keys for one real problem, which is a limit to measure and report, not a defect to hide.
