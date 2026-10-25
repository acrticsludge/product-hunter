from product_hunter.db import connect
from product_hunter.research.contract import load_research_contract, contract_fingerprint
c = connect('db/product_hunter.db')
contract = load_research_contract()
fp = contract_fingerprint(contract)
print('Current contract fingerprint:', fp)
print()
# Check my run
rows = c.execute("SELECT run_id, public_id, contract_version, status, current_stage FROM research_runs WHERE public_id = 'RH-20261025-0001'").fetchall()
print('My run rows:', len(rows))
for r in rows:
    print('  run_id=%s public_id=%s contract_version=%s status=%s stage=%s' % (r['run_id'], r['public_id'], r['contract_version'], r['status'], r['current_stage']))
print()
# Check my run's gate results
run_id = rows[0]['run_id'] if rows else None
if run_id:
    rows2 = c.execute("SELECT stage, contract_fingerprint, passed FROM stage_gate_results WHERE run_id = ? ORDER BY stage", (run_id,)).fetchall()
    print('My run gate results:')
    if not rows2:
        print('  (none yet)')
    else:
        for r in rows2:
            print('  stage=%s fingerprint=%s passed=%s' % (r['stage'], r['contract_fingerprint'], r['passed']))
print()
# Check ALL runs' gate result fingerprints
rows3 = c.execute("""
    SELECT r.public_id, r.contract_version, COUNT(g.gate_result_id) as gate_count,
           GROUP_CONCAT(DISTINCT g.contract_fingerprint) as fps
    FROM research_runs r
    LEFT JOIN stage_gate_results g ON g.run_id = r.run_id
    GROUP BY r.run_id
    ORDER BY r.created_at DESC
""").fetchall()
print('All runs gate result fingerprints:')
for r in rows3:
    print('  %s | contract=%s | gates=%s | fingerprints=%s' % (r['public_id'], r['contract_version'], r['gate_count'], r['fps']))
c.close()
