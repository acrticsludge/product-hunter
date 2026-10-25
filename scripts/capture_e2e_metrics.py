import sqlite3
import json
from product_hunter.db import connect
from product_hunter.research.contract import load_research_contract
from product_hunter.research.gate import evaluate_run, contract_fingerprint
from product_hunter.db.research_gate import build_budget_tracker, load_run_evidence

c = connect('db/product_hunter.db')
run_id = 'run_24ce46618ce35040'

print('=' * 70)
print('PRODUCTION E2E RUN: RH-20261025-0001 (%s)' % run_id)
print('=' * 70)

# Run state
row = c.execute("SELECT * FROM research_runs WHERE run_id = ?", (run_id,)).fetchone()
print()
print('--- RUN STATE ---')
print('  public_id: %s' % row['public_id'])
print('  status: %s' % row['status'])
print('  current_stage: %s' % row['current_stage'])
print('  current_step: %s' % row['current_step'])
print('  progress: %s%%' % row['progress_percent'])
print('  resume_count: %s' % row['resume_count'])
print('  created_at: %s' % row['created_at'])
print('  started_at: %s' % row['started_at'])
print('  completed_at: %s' % row['completed_at'])
print('  last_error: %s' % row['last_error'])
print('  contract_version: %s' % row['contract_version'])
print('  budget_json: %s' % row['budget_json'])

# Jobs
print()
print('--- JOBS ---')
jobs = c.execute("SELECT id, stage, status, attempts, max_attempts, idempotency_key FROM jobs WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
for j in jobs:
    print('  %-24s | %-22s | %-10s | attempts=%s/%s | key=%s' % (
        j['id'], j['stage'], j['status'], j['attempts'], j['max_attempts'], j['idempotency_key']))

# Gate results
print()
print('--- GATE RESULTS ---')
contract = load_research_contract()
print('  Contract version: %s' % contract.contract_version)
print('  Contract fingerprint: %s' % contract_fingerprint(contract))
gates = c.execute("SELECT stage, passed, violations_json, metrics_json, contract_fingerprint FROM stage_gate_results WHERE run_id = ? ORDER BY stage", (run_id,)).fetchall()
all_passed = True
for g in gates:
    fp_short = g['contract_fingerprint'][:16] if g['contract_fingerprint'] else None
    status = 'PASS' if g['passed'] else 'FAIL'
    if not g['passed']:
        all_passed = False
    print('  %-22s | %s | fp=%s' % (g['stage'], status, fp_short))
    if not g['passed']:
        violations = json.loads(g['violations_json']) if g['violations_json'] else []
        for v in violations:
            print('       - %s: %s' % (v['code'], v['message']))
    metrics = json.loads(g['metrics_json']) if g['metrics_json'] else {}
    if metrics:
        print('       metrics: %s' % metrics)

# Run-level gate
print()
print('--- RUN-LEVEL GATE ---')
evidence = None
try:
    evidence = load_run_evidence(c, run_id=run_id, contract_version=contract.contract_version)
except Exception as e:
    print('  Could not load run evidence: %s' % e)

if evidence:
    run_result = evaluate_run(contract, run_id, evidence.stages, build_budget_tracker(c, contract, run_id))
    print('  Run passed: %s' % run_result.passed)
    print('  Run fingerprint: %s' % run_result.contract_fingerprint[:16] if run_result.contract_fingerprint else None)
    for v in run_result.violations:
        print('  - %s: %s' % (v.code, v.message))

# Tool calls
print()
print('--- TOOL CALLS ---')
tc = c.execute("SELECT stage, tool, status, count(*) as n FROM tool_calls WHERE run_id = ? GROUP BY stage, tool, status ORDER BY stage, tool", (run_id,)).fetchall()
for t in tc:
    print('  %-22s | %-12s | %-10s | %d' % (t['stage'], t['tool'], t['status'], t['n']))

# Model executions
print()
print('--- MODEL EXECUTIONS ---')
me = c.execute("SELECT stage, role, provider_id, model_id, selection_source, fallback_index, is_fallback, status, count(*) as n FROM model_executions WHERE run_id = ? GROUP BY stage, role, provider_id, model_id, selection_source, fallback_index, is_fallback, status ORDER BY stage", (run_id,)).fetchall()
for m in me:
    print('  %-22s | %-14s | %-28s | src=%-10s fb=%s status=%-6s | %d' % (
        m['stage'], m['role'], '%s/%s' % (m['provider_id'], m['model_id']),
        m['selection_source'], m['fallback_index'], m['status'], m['n']))

# Evidence
print()
print('--- EVIDENCE ---')
ev_count = c.execute("SELECT count(*) as n FROM evidence").fetchone()
print('  Total evidence spans: %s' % (ev_count['n'] if ev_count else 0))
pp_count = c.execute("SELECT count(*) as n FROM pain_points").fetchone()
print('  Total pain points: %s' % (pp_count['n'] if pp_count else 0))
docs_count = c.execute("SELECT count(*) as n FROM documents").fetchone()
print('  Total documents: %s' % (docs_count['n'] if docs_count else 0))

# Spend ledger
print()
print('--- SPEND LEDGER ---')
spend = c.execute("SELECT stage, tool, unit_type, units, cost_micros, provider, model FROM spend_ledger WHERE run_id = ? ORDER BY stage, unit_type", (run_id,)).fetchall()
if not spend:
    print('  (no spend ledger entries)')
else:
    for s in spend:
        print('  %-22s | %-12s | %-20s | units=%s cost=%s prov=%s model=%s' % (
            s['stage'], s['tool'], s['unit_type'], s['units'], s['cost_micros'],
            s['provider'], s['model']))

# Opportunities
print()
print('--- OPPORTUNITIES ---')
ops = c.execute("SELECT opportunity_id, canonical_id, cluster_id, merged_into, title, normalized_problem, domain, severity, confidence, potential, lifecycle FROM opportunities WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
for o in ops:
    print('  opp_id=%s' % o['opportunity_id'])
    print('    canonical_id=%s cluster_id=%s merged_into=%s' % (o['canonical_id'], o['cluster_id'], o['merged_into']))
    print('    title=%s' % o['title'])
    print('    norm=%s domain=%s severity=%s conf=%s pot=%s lifecycle=%s' % (
        o['normalized_problem'], o['domain'], o['severity'], o['confidence'], o['potential'], o['lifecycle']))

# Opportunity evidence/claims/scores
print()
print('--- OPPORTUNITY LINKS ---')
for o in ops:
    opp_id = o['opportunity_id']
    ev = c.execute("SELECT count(*) as n FROM opportunity_evidence WHERE opportunity_id = ?", (opp_id,)).fetchone()
    cl = c.execute("SELECT count(*) as n FROM opportunity_claims WHERE opportunity_id = ?", (opp_id,)).fetchone()
    sc = c.execute("SELECT count(*) as n FROM opportunity_scores WHERE opportunity_id = ?", (opp_id,)).fetchone()
    print('  %s: evidence=%s claims=%s scores=%s' % (opp_id[:12], ev['n'], cl['n'], sc['n']))

# Projection tasks
print()
print('--- PROJECTION TASKS ---')
projs = c.execute("SELECT id, target, entity_type, entity_id, status, idempotency_key FROM projection_tasks WHERE run_id = ? ORDER BY created_at", (run_id,)).fetchall()
if not projs:
    print('  (no projection tasks)')
else:
    for p in projs:
        print('  %s | target=%s | entity=%s | status=%s' % (p['id'][:12], p['target'], p['entity_type'], p['status']))

c.close()
print()
print('=' * 70)
print('E2E CAPTURE COMPLETE')
print('=' * 70)
