import sqlite3
from product_hunter.fetch import utcnow
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row
now = utcnow()
print('Current time: %s' % now.isoformat())
jobs = c.execute("SELECT id, stage, status, scheduled_for FROM jobs WHERE status = 'queued' ORDER BY created_at").fetchall()
for j in jobs:
    print('  %s | stage=%s | scheduled_for=%s | due=%s' % (
        j['id'][:12], j['stage'], j['scheduled_for'],
        'YES' if j['scheduled_for'] and j['scheduled_for'] <= now.isoformat() else 'NO'))
c.close()
