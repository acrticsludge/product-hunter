from product_hunter.db import connect
from product_hunter.fetch import utcnow
c = connect('db/product_hunter.db')
run_id = 'run_24ce46618ce35040'
now = utcnow()

# Requeue the dead scoring job so the worker can retry it with the fix
c.execute(
    "UPDATE jobs SET status = 'queued', attempts = 0, worker_id = NULL, "
    "lease_expires_at = NULL, lease_epoch = lease_epoch + 1, "
    "scheduled_for = ? WHERE run_id = ? AND stage = 'SCORING' AND status = 'dead'",
    (now.isoformat(), run_id),
)

# Verify
rows = c.execute(
    "SELECT id, stage, status, attempts FROM jobs WHERE run_id = ? AND stage = 'SCORING'",
    (run_id,),
).fetchall()
print('Scoring jobs after requeue:')
for r in rows:
    print('  job=%s stage=%s status=%s attempts=%s' % (r['id'], r['stage'], r['status'], r['attempts']))

c.commit()
c.close()
print('Scoring job requeued.')
