from product_hunter.db import connect
c = connect('db/product_hunter.db')

# All opportunities with non-null canonical_id
rows = c.execute("""
    SELECT opportunity_id, canonical_id, run_id, title
    FROM opportunities 
    WHERE canonical_id IS NOT NULL
    ORDER BY created_at
    LIMIT 30
""").fetchall()
print('All opportunities with non-null canonical_id:')
for r in rows:
    print('  opp_id=%s canonical=%s run=%s title=%s' % (
        r['opportunity_id'], r['canonical_id'], r['run_id'], r['title'][:40]))

# Check for any canonical_id used by multiple opportunity_ids
print()
rows2 = c.execute("""
    SELECT canonical_id, count(*) as cnt
    FROM opportunities 
    WHERE canonical_id IS NOT NULL
    GROUP BY canonical_id
    HAVING cnt > 1
""").fetchall()
print('Canonical IDs used by multiple opportunities: %d' % len(rows2))
for r in rows2:
    print('  canonical_id=%s count=%s' % (r['canonical_id'], r['cnt']))

# Total opportunities
total = c.execute("SELECT count(*) as n FROM opportunities").fetchone()
print()
print('Total opportunities: %s' % total['n'])

c.close()
