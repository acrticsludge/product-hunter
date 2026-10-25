from product_hunter.db import connect
c = connect('db/product_hunter.db', read_only=True)
run_id = 'run_24ce46618ce35040'

print('GATE VIOLATIONS:')
rows = c.execute("""
    SELECT stage, violations_json FROM stage_gate_results WHERE run_id = ? ORDER BY stage
""", (run_id,)).fetchall()
if not rows:
    print('  (none yet)')
else:
    import json
    for r in rows:
        stage = r['stage']
        vjs = r['violations_json']
        if not vjs:
            print('  %s: (no violations, passed=1)' % stage)
            continue
        violations = json.loads(vjs)
        print('  %s:' % stage)
        for v in violations:
            print('    - %s: %s' % (v['code'], v['message']))

c.close()
