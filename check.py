import sqlite3
import json

conn = sqlite3.connect("logs.db")
conn.row_factory = sqlite3.Row
cur = conn.cursor()

# 1. Row count
cur.execute("SELECT COUNT(*) FROM email_insights")
count = cur.fetchone()[0]
print("=== DATABASE ===")
print(f"Row count: {count}")

# 2. Sample rows
if count > 0:
    cur.execute("SELECT * FROM email_insights LIMIT 3")
    rows = cur.fetchall()
    for i, r in enumerate(rows):
        print(f"\n--- Row {i+1} ---")
        for k in r.keys():
            print(f"  {k}: {r[k]}")
else:
    print("NO RECORDS in email_insights table")

# 3. Check table schema
cur.execute("PRAGMA table_info(email_insights)")
cols = cur.fetchall()
print("\n=== SCHEMA email_insights ===")
for c in cols:
    print(f"  {c['name']} ({c['type']})")

# Now simulate the API endpoint
print("\n=== SIMULATED API RESPONSE (get_email_insights page=1, page_size=5) ===")
offset = 0
cur.execute(
    "SELECT id, gmail_message_id, sender, subject, summary, category, "
    "priority, action_required, important_note, is_read, created_at "
    "FROM email_insights ORDER BY created_at DESC LIMIT 5 OFFSET 0"
)
rows = cur.fetchall()
items = []
for row in rows:
    items.append({
        "id": row[0],
        "gmail_message_id": row[1],
        "sender": row[2],
        "subject": row[3],
        "summary": row[4],
        "category": row[5],
        "priority": row[6],
        "action_required": bool(row[7]),
        "important_note": row[8],
        "is_read": bool(row[9]),
        "created_at": row[10],
    })

response = {
    "items": items,
    "total": count,
    "page": 1,
    "page_size": 5,
}
print(json.dumps(response, indent=2, ensure_ascii=False))

# 4. Check user_auth table
print("\n=== user_auth ===")
cur.execute("SELECT COUNT(*) FROM user_auth")
ua_count = cur.fetchone()[0]
print(f"Row count: {ua_count}")
if ua_count > 0:
    cur.execute("SELECT * FROM user_auth LIMIT 1")
    r = cur.fetchone()
    for k in r.keys():
        val = r[k]
        if k == "password_hash":
            val = val[:20] + "..." if val else None
        print(f"  {k}: {val}")

conn.close()
