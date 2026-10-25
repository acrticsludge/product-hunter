import sqlite3
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row

# Check stage_attempts schema
cols = c.execute('PRAGMA table_info(stage_attempts)').fetchall()
print('stage_attempts schema:')
for col in cols:
    print('  %s: %s' % (col['name'], col['type']))

# Check existing attempts for the scoring job
rows = c.execute(
    "SELECT * FROM stage_attempts WHERE job_id = ? ORDER BY attempt",
    ('job_175272e088a84f88',),
).fetchall()
print()
print('Stage attempts for scoring job:')
for r in rows:
    print('  attempt=%s status=%s started=%s finished=%s error=%s' % (
        r['attempt'], r['status'], r['started_at'], r['finished_at'], r['error']))

c.close()
