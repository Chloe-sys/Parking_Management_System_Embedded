import mysql.connector
from mysql.connector import Error

try:
    conn = mysql.connector.connect(
        host='localhost',
        user='root',
        password='',
        database='parking_system_intelligent'
    )
    if conn.is_connected():
        print("✅ Connection successful!")
        conn.close()
    else:
        print("❌ Connection failed.")
except Error as e:
    print(f"MySQL Error: {e}")