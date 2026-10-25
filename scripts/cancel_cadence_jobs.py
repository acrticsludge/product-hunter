import sqlite3
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row

# Cancel cadence jobs (run_id is null) that block research-run jobs
rows = c.execute(
    "SELECT id, stage, status FROM jobs WHERE run_id IS NULL AND status = 'queued'"
).fetchall()
print('Cadence jobs to cancel: %d' % len(rows))
for r in rows:
    print('  job=%s stage=%s' % (r['id'][:12], r['stage']))
    c.execute(
        "UPDATE jobs SET status = 'cancelled' WHERE id = ?",
        (r['id'],),
    )

c.commit()

# Verify SCORING job is now claimable
jobs = c.execute(
    "SELECT id, run_id, stage, status FROM jobs WHERE status = 'queued' ORDER BY created_at"
).fetchall()
print()
print('Remaining queued jobs: %d' % len(jobs))
for j in jobs:
    rid = j['run_id'] if j['run_id'] else '(null)'
    print('  %s | run=%s | stage=%s' % (j['id'][:12], rid[:12] if rid != '(null)' else rid, j['stage']))

c.close()
print('Cadence jobs cancelled.')
