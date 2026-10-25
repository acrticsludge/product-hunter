from product_hunter.db import connect
from product_hunter.registry import list_runs, doctor
c = connect('db/product_hunter.db', read_only=True)
print('=== DOCTOR ===')
d = doctor(c)
for k, v in d.items():
    print('  %s: %s' % (k, v))
print()
print('=== RUNS ===')
runs = list_runs(c)
for r in runs:
    wa = 'yes' if r.worker_active else 'no'
    print('  %s | %s | stage=%s | progress=%s%% | worker=%s | obj=%s' % (r.public_id, r.status, r.current_stage, r.progress_percent, wa, r.objective[:60]))
c.close()
