import sqlite3

conn = sqlite3.connect("logs.db")
cur = conn.cursor()

cur.execute("""
SELECT event_id, agent, status, payload, timestamp
FROM system_logs
WHERE status='meeting_accepted'
ORDER BY timestamp DESC
""")

rows = cur.fetchall()

for r in rows:
    print(r)

print("TOTAL:", len(rows))
