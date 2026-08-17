import sqlite3

def clean():
    conn = sqlite3.connect('data/qualifierscout.sqlite3')
    cursor = conn.cursor()
    cursor.execute("DELETE FROM leads WHERE (contractor_name IS NULL OR contractor_name = '') AND (license_number IS NULL OR license_number = '')")
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"Deleted {deleted} blank rows.")

if __name__ == '__main__':
    clean()
