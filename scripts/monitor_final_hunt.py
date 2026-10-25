import sqlite3
from product_hunter.fetch import utcnow
c = sqlite3.connect('db/product_hunter.db')
c.row_factory = sqlite3.Row
run_id = 'run_eb91bb88fa2a9b61'

print('=== RUN STATE ===')
row = c.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
if row:
    print('public_id: %s' % row['public_id'])
    print('status: %s' % row['status'])
    print('current_stage: %s' % row['current_stage'])
    print('current_step: %s' % row['current_step'])
    print('progress: %s%%' % row['progress_percent'])
    print('resume_count: %s' % row['resume_count'])
    print('started_at: %s' % row['started_at'])
    print('completed_at: %s' % row['completed_at'])
    print('last_error: %s' % row['last_error'])
else:
    print('RUN NOT FOUND')
    c.close()
    raise SystemExit

print()
print('=== JOBS ===')
jobs = c.execute("SELECT id, stage, status, attempts, max_attempts, idempotency_key FROM jobs WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
for j in jobs:
    print('  %-24s | %-22s | %-10s | attempts=%s/%s | key=%s' % (
        j['id'], j['stage'], j['status'], j['attempts'], j['max_attempts'], j['idempotency_key']))

print()
print('=== GATE RESULTS ===')
gates = c.execute("SELECT stage, passed, violations_json FROM stage_gate_results WHERE run_id = ? ORDER BY stage", (run_id,)).fetchall()
if not gates:
    print('  (none yet)')
else:
    import json
    for g in gates:
        status = 'PASS' if g['passed'] else 'FAIL'
        print('  %-22s | %s' % (g['stage'], status))
        if not g['passed']:
            violations = json.loads(g['violations_json']) if g['violations_json'] else []
            for v in violations:
                print('       - %s: %s' % (v['code'], v['message']))

print()
print('=== TOOL CALLS ===')
tc = c.execute("SELECT stage, tool, status, count(*) as n FROM tool_calls WHERE run_id = ? GROUP BY stage, tool, status ORDER BY stage, tool", (run_id,)).fetchall()
if not tc:
    print('  (none yet)')
else:
    for t in tc:
        print('  %-22s | %-12s | %-10s | %d' % (t['stage'], t['tool'], t['status'], t['n']))

print()
print('=== MODEL EXECUTIONS ===')
me = c.execute("SELECT stage, role, provider_id, model_id, status, count(*) as n FROM model_executions WHERE run_id = ? GROUP BY stage, role, provider_id, model_id, status ORDER BY stage", (run_id,)).fetchall()
if not me:
    print('  (none yet)')
else:
    for m in me:
        print('  %-22s | %-14s | %-28s | status=%-6s | %d' % (
            m['stage'], m['role'], '%s/%s' % (m['provider_id'], m['model_id']), m['status'], m['n']))

print()
print('=== EVIDENCE ===')
ev = c.execute("SELECT count(*) as n FROM evidence").fetchone()
print('  evidence spans: %s' % (ev['n'] if ev else 0))
pp = c.execute("SELECT count(*) as n FROM pain_points").fetchone()
print('  pain points: %s' % (pp['n'] if pp else 0))
docs = c.execute("SELECT count(*) as n FROM documents").fetchone()
print('  documents: %s' % (docs['n'] if docs else 0))

print()
print('=== OPPORTUNITIES ===')
ops = c.execute("SELECT opportunity_id, canonical_id, cluster_id, merged_into, title, normalized_problem, domain, severity, confidence, potential, lifecycle FROM opportunities WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
if not ops:
    print('  (none yet)')
else:
    for o in ops:
        print('  opp_id=%s' % o['opportunity_id'])
        print('    canonical_id=%s cluster_id=%s merged_into=%s' % (o['canonical_id'], o['cluster_id'], o['merged_into']))
        print('    title=%s' % o['title'])
        print('    norm=%s domain=%s severity=%s conf=%s pot=%s lifecycle=%s' % (
            o['normalized_problem'], o['domain'], o['severity'], o['confidence'], o['potential'], o['lifecycle']))

c.close()
