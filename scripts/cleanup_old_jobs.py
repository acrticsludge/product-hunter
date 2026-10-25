from product_hunter.db import connect
c = connect('db/product_hunter.db')

# Mark all old runs' queued/failed/dead jobs as cancelled (except my run)
my_run_id = "run_24ce46618ce35040"
rows = c.execute(
    "SELECT id, run_id, stage, status FROM jobs WHERE status IN ('queued', 'failed', 'dead', 'leased') AND run_id != ?",
    (my_run_id,),
).fetchall()
print('Old jobs to cancel: %d' % len(rows))
for r in rows:
    print('  job=%s run=%s stage=%s status=%s' % (r['id'], r['run_id'], r['stage'], r['status']))
    c.execute("UPDATE jobs SET status = 'cancelled', worker_id = NULL, lease_expires_at = NULL WHERE id = ?", (r['id'],))

c.commit()

# Verify my run's job is still queued
my_jobs = c.execute("SELECT id, stage, status FROM jobs WHERE run_id = ?", (my_run_id,)).fetchall()
print()
print('My run jobs:')
for r in my_jobs:
    print('  job=%s stage=%s status=%s' % (r['id'], r['stage'], r['status']))

c.close()
print('Cleanup done.')
