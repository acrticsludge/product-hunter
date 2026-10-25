from product_hunter.db import connect
c = connect('db/product_hunter.db')

# Show all opportunities with canonical_id conflicts
rows = c.execute("""
    SELECT canonical_id, count(*) as cnt, GROUP_CONCAT(opportunity_id) as ids
    FROM opportunities 
    WHERE canonical_id IS NOT NULL 
    GROUP BY canonical_id 
    HAVING cnt > 1
""").fetchall()
print('Canonical ID conflicts (multiple opportunity_ids per canonical_id):')
for r in rows:
    print('  canonical_id=%s count=%s ids=%s' % (r['canonical_id'], r['cnt'], r['ids']))

# Show all opportunities for this run
print()
print('All opportunities for this run:')
rows2 = c.execute("""
    SELECT opportunity_id, canonical_id, cluster_id, title, normalized_problem
    FROM opportunities 
    WHERE run_id = 'run_24ce46618ce35040'
    ORDER BY created_at
""").fetchall()
for r in rows2:
    print('  opp_id=%s canonical=%s cluster=%s title=%s norm=%s' % (
        r['opportunity_id'], r['canonical_id'], r['cluster_id'],
        r['title'][:40], r['normalized_problem']))

# Show duplicate normalized_keys in pain_points
print()
print('Duplicate normalized_keys in pain_points:')
rows3 = c.execute("""
    SELECT normalized_key, count(*) as cnt
    FROM pain_points 
    WHERE normalized_key IS NOT NULL
    GROUP BY normalized_key
    HAVING cnt > 1
""").fetchall()
for r in rows3:
    print('  key=%s count=%s' % (r['normalized_key'], r['cnt']))

c.close()
