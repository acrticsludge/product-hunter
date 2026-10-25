import sqlite3
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row

# Projection tasks schema
cols = c.execute('PRAGMA table_info(projection_tasks)').fetchall()
print('projection_tasks schema:')
for col in cols:
    print('  %s: %s' % (col['name'], col['type']))
print()

# Projection tasks for this run
projs = c.execute("SELECT * FROM projection_tasks WHERE entity_id = ? ORDER BY created_at", ('run_24ce46618ce35040',)).fetchall()
print('Projection tasks for run:')
if not projs:
    print('  (none)')
else:
    for p in projs:
        print('  %s' % dict(p))

# OBSIDIAN_SYNC job error
job = c.execute("SELECT * FROM jobs WHERE stage = 'OBSIDIAN_SYNC' AND run_id = ? ORDER BY created_at DESC LIMIT 1", ('run_24ce46618ce35040',)).fetchone()
if job:
    print()
    print('OBSIDIAN_SYNC job:')
    print('  status: %s' % job['status'])
    print('  attempts: %s/%s' % (job['attempts'], job['max_attempts']))
    print('  last_error: %s' % job['last_error'])

# Also check the worker log for OBSIDIAN_SYNC errors
c.close()
print()
print('DONE')
