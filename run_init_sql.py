"""
Run this once to set up your Render database -- creates tables, enables
PostGIS, and seeds the initial staff/admin accounts. Needs only psycopg2,
not a local psql install.

Note: as of the password-auth update, backend.py already runs this exact
migration automatically on every startup (see run_migrations() in
backend.py), so on Render you likely don't need to run this file by hand
at all -- it's kept around for manual/offline setup or troubleshooting.

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
        cur.execute("SELECT phone, name, is_staff, is_admin, password_hash IS NOT NULL AS has_password FROM users ORDER BY id;")
        rows = cur.fetchall()
    print("\nSeeded users:")
    for phone, name, is_staff, is_admin, has_password in rows:
        role = "admin" if is_admin else ("staff" if is_staff else "citizen")
        pw_state = "password set" if has_password else "NEEDS PASSWORD (first login will prompt for one)"
        print(f"  {phone}  {name}  ({role}, {pw_state})")
finally:
    conn.close()