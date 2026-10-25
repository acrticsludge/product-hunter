import sqlite3
c = sqlite3.connect('db/product_hunter.db')
schema = c.execute("SELECT sql FROM sqlite_master WHERE name='stage_attempts'").fetchone()
if schema:
    print(schema[0])
else:
    print('stage_attempts table not found')
c.close()
