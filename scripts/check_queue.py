import sqlite3
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row
jobs = c.execute("SELECT id, run_id, stage, status FROM jobs WHERE status = ? ORDER BY created_at", ('queued',)).fetchall()
print('Total queued: %d' % len(jobs))
for j in jobs:
    rid = j['run_id'] if j['run_id'] else '(null)'
    print('  %s | run=%s | stage=%s' % (j['id'][:12], rid[:12] if rid != '(null)' else rid, j['stage']))
c.close()
