"""Reconcile historical duplicate opportunities into canonical identities.

Read-only analysis first, then writes:
  1. Build opportunity -> cluster mapping via evidence -> pain links.
  2. Group opportunities by cluster (falling back to normalized_problem).
  3. For each group with more than one opportunity, pick a survivor
     (earliest created_at; tie-break by highest potential), set
     merged_into on the others, and relink their evidence spans and claims
     onto the survivor.
  4. Backfill canonical_id on every opportunity (cluster_id or stable hash
     of normalized_problem) so future canonical writes converge.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone

DB = "db/product_hunter.db"


def canonical_id_for(key: str) -> str:
    return "opp_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def main() -> None:
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    # --- 1. opportunity -> cluster via evidence -> pain -> cluster_members ---
    opp_cluster: dict[str, str] = {}
    rows = conn.execute(
        """
        SELECT DISTINCT oe.opportunity_id, cm.cluster_id
        FROM opportunity_evidence oe
        JOIN evidence_pain_links l ON l.span_id = oe.span_id
        JOIN cluster_members cm ON cm.pain_id = l.pain_id AND cm.valid_to IS NULL
        """
    ).fetchall()
    for r in rows:
        # A cluster is the authority; if an opportunity touches multiple
        # clusters (should not happen), keep the first.
        opp_cluster.setdefault(r["opportunity_id"], r["cluster_id"])

    # --- 2. load all opportunities ---
    opps = conn.execute(
        "SELECT opportunity_id, run_id, title, normalized_problem, "
        "cluster_id, canonical_id, merged_into, created_at, potential "
        "FROM opportunities"
    ).fetchall()

    groups: dict[str, list[dict]] = defaultdict(list)
    unclustered: dict[str, list[dict]] = defaultdict(list)

    for r in opps:
        oid = r["opportunity_id"]
        cluster_id = opp_cluster.get(oid) or r["cluster_id"]
        if cluster_id:
            groups[cluster_id].append(dict(r))
        else:
            key = r["normalized_problem"] or r["title"]
            unclustered[key].append(dict(r))

    # --- 3. reconcile each group ---
    merged = 0
    survivor_count = 0

    def reconcile(group: list[dict]) -> None:
        nonlocal merged, survivor_count
        if len(group) <= 1:
            survivor_count += 1
            return
        # Survivor: earliest created_at, then highest potential.
        group.sort(key=lambda r: (r["created_at"] or "", -(r["potential"] or 0)))
        survivor = group[0]
        duplicates = group[1:]
        survivor_id = survivor["opportunity_id"]
        # Ensure survivor has a canonical_id.
        if not survivor["canonical_id"]:
            key = opp_cluster.get(survivor_id) or (survivor["normalized_problem"] or survivor["title"])
            conn.execute(
                "UPDATE opportunities SET canonical_id = ? WHERE opportunity_id = ?",
                (canonical_id_for(key), survivor_id),
            )
        for dup in duplicates:
            dup_id = dup["opportunity_id"]
            if dup_id == survivor_id:
                continue
            # Relink evidence spans to survivor.
            conn.execute(
                """
                INSERT OR IGNORE INTO opportunity_evidence (opportunity_id, span_id, stance, created_at)
                SELECT ?, span_id, stance, created_at
                FROM opportunity_evidence WHERE opportunity_id = ?
                """,
                (survivor_id, dup_id),
            )
            # Relink claims to survivor (new claim ids).
            claim_rows = conn.execute(
                "SELECT statement, kind, span_id, created_at FROM opportunity_claims WHERE opportunity_id = ?",
                (dup_id,),
            ).fetchall()
            for cr in claim_rows:
                claim_digest = hashlib.sha256(
                    (dup_id + "|" + (cr["statement"] or "") + "|" + str(cr["created_at"])).encode("utf-8")
                ).hexdigest()[:12]
                conn.execute(
                    """
                    INSERT OR IGNORE INTO opportunity_claims (claim_id, opportunity_id, statement, kind, span_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"claim_merged_{claim_digest}",
                        survivor_id,
                        cr["statement"],
                        cr["kind"],
                        cr["span_id"],
                        cr["created_at"],
                    ),
                )
            # Mark duplicate as merged into survivor (audit trail, row kept).
            conn.execute(
                "UPDATE opportunities SET merged_into = ? WHERE opportunity_id = ?",
                (survivor_id, dup_id),
            )
            merged += 1
        survivor_count += 1
        conn.commit()

    for key, group in groups.items():
        reconcile(group)
    for key, group in unclustered.items():
        reconcile(group)

    # --- 4. backfill canonical_id on any living opportunity still missing it ---
    missing = conn.execute(
        "SELECT opportunity_id, cluster_id, normalized_problem, title FROM opportunities "
        "WHERE canonical_id IS NULL AND merged_into IS NULL"
    ).fetchall()
    for r in missing:
        key = r["cluster_id"] or (r["normalized_problem"] or r["title"])
        conn.execute(
            "UPDATE opportunities SET canonical_id = ? WHERE opportunity_id = ?",
            (canonical_id_for(key), r["opportunity_id"]),
        )
    conn.commit()

    # --- report ---
    total = conn.execute("SELECT count(*) FROM opportunities").fetchone()[0]
    living = conn.execute(
        "SELECT count(*) FROM opportunities WHERE merged_into IS NULL"
    ).fetchone()[0]
    dups = total - living
    print(f"Total opportunities: {total}")
    print(f"Living (canonical):  {living}")
    print(f"Merged duplicates:   {dups}")
    print(f"Groups reconciled:   {survivor_count}")
    print(f"Duplicate rows:      {merged}")
    conn.close()


if __name__ == "__main__":
    main()
