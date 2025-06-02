import mysql.connector
from mysql.connector import Error
from tabulate import tabulate
from datetime import datetime
from config import DB_CONFIG

def print_table(cursor, table_name):
    try:
        cursor.execute(f"SELECT * FROM {table_name}")
        rows = cursor.fetchall()
        
        # Get column names
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        columns = [col[0] for col in cursor.fetchall()]
        
        if rows:
            print(f"\n=== {table_name.upper()} ===")
            print(tabulate(rows, headers=columns, tablefmt='grid'))
        else:
            print(f"\n=== {table_name.upper()} ===")
            print("No data found")
    except Error as e:
        print(f"Error accessing table {table_name}: {e}")

def main():
    conn = None
    try:
        print("Attempting to connect to MySQL database...")
        print(f"Connection parameters: {DB_CONFIG}")
        
        # Connect to the database
        conn = mysql.connector.connect(**DB_CONFIG)
        
        if conn.is_connected():
            print("Successfully connected to MySQL database!")
            cursor = conn.cursor()
            
            # Get list of tables
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            
            if not tables:
                print("No tables found in the database!")
                return
                
            print("\nDatabase Tables:")
            for table in tables:
                print(f"- {table[0]}")
                
            # Print contents of each table
            for table in tables:
                print_table(cursor, table[0])
        else:
            print("Failed to connect to the database!")
            
    except Error as e:
        print(f"MySQL Error: {e}")
        print("\nTroubleshooting steps:")
        print("1. Make sure MySQL server is running")
        print("2. Verify the database 'parking_system_intelligent' exists")
        print("3. Check if the user 'chloe' has proper permissions")
        print("4. Verify the password is correct")
        print("5. Ensure MySQL is running on port 3306")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()
            print("\nDatabase connection closed.")

if __name__ == "__main__":
    main() 