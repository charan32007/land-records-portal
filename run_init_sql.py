"""
Run this once to set up your Render database -- creates tables, enables
PostGIS, and seeds the staff/policymaker accounts. Needs only psycopg2,
not a local psql install.

Usage:
    pip install psycopg2-binary
    python run_init_sql.py "<your External Database URL from Render>"
"""
import sys
import psycopg2

if len(sys.argv) != 2:
    print('Usage: python run_init_sql.py "<External Database URL>"')
    sys.exit(1)

database_url = sys.argv[1]

with open("init.sql", "r", encoding="utf-8") as f:
    sql = f.read()

conn = psycopg2.connect(database_url)
conn.autocommit = True
try:
    with conn.cursor() as cur:
        cur.execute(sql)
    print("init.sql ran successfully.")

    with conn.cursor() as cur:
        cur.execute("SELECT phone, name, is_staff, is_policymaker FROM users ORDER BY id;")
        rows = cur.fetchall()
    print("\nSeeded users:")
    for phone, name, is_staff, is_policymaker in rows:
        role = "staff" if is_staff else ("policymaker" if is_policymaker else "citizen")
        print(f"  {phone}  {name}  ({role})")
finally:
    conn.close()