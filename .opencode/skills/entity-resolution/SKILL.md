---
name: entity-resolution
description: Use when deciding whether two accounts are the same person, or when an identity link would change a count.
---

# Entity resolution

Two records are the same person only when the evidence supports it. Otherwise they stay separate records, and a merge is a defect to fix rather than a tie to break.

This skill implements §12 and the identity half of §15A. The `author_links` table in `db/migrations/0001_initial.sql` exists today, and the merge and split logic lands in Phase 3.

## The signals

| Signal | Weight |
|---|---|
| platform with handle | strong when the platform is the same |
| hashed identity key | strong when the key matches |
| display-name similarity | weak on its own |
| community overlap | supporting |
| cross-linking between accounts | supporting |
| signature or phrasing reuse | weak on its own |

Never merge on display name alone. A merge produced by a name match is a defect, whatever the name looks like.

## Merge, or suspect edge

Every merge and every suspect edge records the method that produced it and a confidence. A link below the merge threshold is a `same-person-suspect` edge, not a merge. A pseudonymous account is not a person until it is resolved, so it never enters the independent-author count as one.

## Reversible

Merges are reversible. The edge is stored so a split can be recomputed, and a later run that disagrees with a merge does not silently delete it. The disagreeing run records its own method and confidence beside the original.

## Identity keys are hashed

Identity keys are hashed before storage. A raw identifier never reaches a report, an export, or an Obsidian note.

## Deletion

A deleted account is marked with a deletion status and stays in the count history. Removing it would change a number that a past report already used, so the record stays and the status carries the change.

## Fail closed

An unresolved author is reported as uncertain, and uncertain is never rounded up. No merge is written without its method and its confidence.
