# database.py

import sqlite3
from typing import Dict, Any, Optional, List

class DB:
    def __init__(self, db_path='clg_gpt.db'):
        # NOTE: On Render, we will use the in-memory DB or a service like MongoDB, 
        # but for now, we point to the file.
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.initialize_db()

    def initialize_db(self):
        # Create the users table if it doesn't exist
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                hashed_password TEXT NOT NULL,
                full_name TEXT,
                username TEXT UNIQUE,
                branch TEXT,
                usn TEXT UNIQUE,
                study_year INTEGER,
                role TEXT
            )
        """)
        self.conn.commit()

    def create_user(self, user_dict: Dict[str, Any]) -> None:
        self.cursor.execute(
            """
            INSERT INTO users (email, hashed_password, full_name, username, branch, usn, study_year, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_dict['email'], user_dict['hashed_password'], user_dict['full_name'], 
             user_dict['username'], user_dict['branch'], user_dict['usn'], 
             user_dict['study_year'], user_dict['role'])
        )
        self.conn.commit()

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        self.cursor.execute("SELECT * FROM users WHERE email=?", (email,))
        row = self.cursor.fetchone()
        if row:
            # Manually map column names to values since dict factory isn't used
            columns = [desc[0] for desc in self.cursor.description]
            return dict(zip(columns, row))
        return None

    # You would also need methods like find_user_by_usn, etc.