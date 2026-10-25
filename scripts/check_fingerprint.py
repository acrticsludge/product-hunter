from product_hunter.db import connect
from product_hunter.research.contract import load_research_contract, contract_fingerprint
c = connect('db/product_hunter.db')
# Check contract fingerprint
contract = load_research_contract()
fp = contract_fingerprint(contract)
print('Current contract fingerprint:', fp)
print('Contract version:', contract.contract_version)
# Check gate results fingerprints
rows = c.execute("SELECT DISTINCT contract_fingerprint, count(*) FROM stage_gate_results GROUP BY contract_fingerprint").fetchall()
print('Gate result fingerprints in DB:')
for r in rows:
    print('  fingerprint=%s count=%s' % (r[0], r[1]))
# Check research_runs contract versions
rows2 = c.execute("SELECT DISTINCT contract_version, count(*) FROM research_runs GROUP BY contract_version").fetchall()
print('Research run contract versions:')
for r in rows2:
    print('  version=%s count=%s' % (r[0], r[1]))
# Check for my run
rows3 = c.execute("SELECT run_id, public_id, contract_version, status, current_stage FROM research_runs WHERE public_id = 'RH-20261025-0001'").fetchall()
print('My run:')
for r in rows3:
    print('  run_id=%s public_id=%s contract_version=%s status=%s stage=%s' % r)
c.close()
