import sqlite3
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row

job_id = 'job_175272e088a84f88'

# Delete old stage_attempts rows for this job so the retry can insert fresh ones
deleted = c.execute(
    "DELETE FROM stage_attempts WHERE job_id = ?",
    (job_id,),
).rowcount
print('Deleted %d stage_attempts rows for job %s' % (deleted, job_id))

# Verify the job is still queued with attempts=0
job = c.execute(
    "SELECT id, stage, status, attempts FROM jobs WHERE id = ?",
    (job_id,),
).fetchone()
print('Job: id=%s stage=%s status=%s attempts=%s' % (
    job['id'], job['stage'], job['status'], job['attempts']))

c.commit()
c.close()
print('Stage attempts cleared.')
