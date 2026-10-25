from product_hunter.db import connect
c = connect('db/product_hunter.db', read_only=True)
run_id = 'run_24ce46618ce35040'

# Run state
row = c.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
if row:
    print('RUN: public_id=%s status=%s stage=%s step=%s progress=%s%% resume=%s' % (
        row['public_id'], row['status'], row['current_stage'], row['current_step'],
        row['progress_percent'], row['resume_count']))
    print('  started=%s completed=%s last_error=%s' % (
        row['started_at'], row['completed_at'], row['last_error']))
else:
    print('RUN NOT FOUND')
    c.close()
    raise SystemExit

# Jobs
print()
print('JOBS:')
jobs = c.execute("SELECT id, stage, status, attempts, max_attempts, idempotency_key FROM jobs WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
for j in jobs:
    print('  %s | %s | %s | attempts=%s/%s | key=%s' % (j['id'], j['stage'], j['status'], j['attempts'], j['max_attempts'], j['idempotency_key']))

# Gate results
print()
print('GATE RESULTS:')
gates = c.execute("SELECT stage, passed, contract_fingerprint FROM stage_gate_results WHERE run_id = ? ORDER BY stage", (run_id,)).fetchall()
if not gates:
    print('  (none yet)')
else:
    for g in gates:
        fp = g['contract_fingerprint']
        print('  %s | passed=%s | fingerprint=%s' % (g['stage'], g['passed'], fp[:16] if fp else None))

# Tool calls
print()
print('TOOL CALLS:')
tc = c.execute("SELECT stage, tool, status, count(*) as n FROM tool_calls WHERE run_id = ? GROUP BY stage, tool, status ORDER BY stage, tool", (run_id,)).fetchall()
if not tc:
    print('  (none yet)')
else:
    for t in tc:
        print('  %s | %s | %s | %d' % (t['stage'], t['tool'], t['status'], t['n']))

# Model executions
print()
print('MODEL EXECUTIONS:')
me = c.execute("SELECT stage, role, provider_id, model_id, status, count(*) as n FROM model_executions WHERE run_id = ? GROUP BY stage, role, provider_id, model_id, status ORDER BY stage", (run_id,)).fetchall()
if not me:
    print('  (none yet)')
else:
    for m in me:
        print('  %s | %s | %s/%s | %s | %d' % (m['stage'], m['role'], m['provider_id'], m['model_id'], m['status'], m['n']))

# Evidence
print()
print('EVIDENCE:')
ev = c.execute("SELECT count(*) as n FROM evidence").fetchone()
print('  evidence spans: %s' % (ev['n'] if ev else 0))
pp = c.execute("SELECT count(*) as n FROM pain_points").fetchone()
print('  pain points: %s' % (pp['n'] if pp else 0))
docs = c.execute("SELECT count(*) as n FROM documents").fetchone()
print('  total documents: %s' % (docs['n'] if docs else 0))

# Opportunities
print()
print('OPPORTUNITIES:')
ops = c.execute("SELECT opportunity_id, canonical_id, cluster_id, title, lifecycle, confidence, potential FROM opportunities WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
if not ops:
    print('  (none yet)')
else:
    for o in ops:
        print('  %s | canonical=%s | cluster=%s | %s | %s | conf=%s | pot=%s' % (
            o['opportunity_id'], o['canonical_id'], o['cluster_id'],
            o['title'][:50], o['lifecycle'], o['confidence'], o['potential']))

c.close()
