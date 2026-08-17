import sqlite3
import os

db_path = os.path.join(os.path.dirname(__file__), '..', 'data', 'qualifierscout.sqlite3')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

cursor.execute("DELETE FROM leads WHERE state = 'NM' OR state = 'New Mexico'")
print(f"Successfully deleted {cursor.rowcount} records from New Mexico!")

conn.commit()
conn.close()
