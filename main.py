from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, EmailStr, Field, validator
from pydantic_core import PydanticCustomError
from google import genai
from google.genai import types
from google.genai.errors import APIError
from dotenv import load_dotenv
from passlib.context import CryptContext
import os
import sqlite3
import re
from typing import Literal
import json 
from fastapi.middleware.cors import CORSMiddleware 

# --- Configuration & Initialization ---
load_dotenv() 

# --- START: Updated API Key/Client Initialization for Deployment ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY") 
DB_NAME = "clg_gpt.db"

# Initialize Gemini Client
gemini_client = None
if GEMINI_API_KEY:
    try:
        # The client automatically uses the GEMINI_API_KEY from the environment
        # but we explicitly pass it for clarity since we are checking for it.
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except ValueError:
        # This occurs if the key is structurally invalid
        print("Warning: GEMINI_API_KEY is structurally invalid.")
else:
    print("Warning: GEMINI_API_KEY environment variable not found. AI chat endpoint will be disabled.")
# --- END: Updated API Key/Client Initialization ---

pwd_context = CryptContext(schemes=["sha256_crypt"], deprecated="auto")

# Initialize FastAPI application
app = FastAPI(title="Clg GPT Backend API")

# --- CORS Middleware Configuration ---
origins = [
    "http://localhost",
    "http://localhost:8080", 
    "http://127.0.0.1:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- END CORS BLOCK ---

# Mock data structure simulating Google Drive folder links for notes
NOTES_LINK_MAP = {
    "CS": "https://drive.google.com/drive/folders/CS_2025_Syllabus_Notes_Link",
    "AI": "https://drive.google.com/drive/folders/AI_2025_Notes_Repository",
    "IS": "https://drive.google.com/drive/folders/IS_Core_Materials_Shared",
    "EC": "https://drive.google.com/drive/folders/EC_Sem_Materials",
    "CSBS": "https://drive.google.com/drive/folders/CSBS_Notes_Link",
    "CSD": "https://drive.google.com/drive/folders/CSD_Notes_Link",
}

# --- Database Setup ---
def init_db():
    """Initializes the SQLite database and creates all necessary tables."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # 1. Users Table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            email TEXT PRIMARY KEY,
            full_name TEXT NOT NULL,
            username TEXT UNIQUE NOT NULL,
            branch TEXT,
            usn TEXT UNIQUE, 
            study_year INTEGER,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL
        )
    """)
    
    # 2. Jobs Table (for Placement Cell)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            company TEXT NOT NULL,
            description TEXT,
            eligibility_branch TEXT,
            application_link TEXT
        )
    """)
    
    # 3. Schedules Table (for Student Timetables)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            usn_prefix TEXT PRIMARY KEY, 
            schedule TEXT NOT NULL
        )
    """)

    # Seed initial mock schedule data
    mock_schedules = {
        "4cb23cs": {
            "Monday": "9:00 AM - 10:00 AM: DSA, 10:00 AM - 11:00 AM: DBMS Lab",
            "Tuesday": "9:00 AM - 12:00 PM: Project Meeting, 2:00 PM - 3:00 PM: Workshop",
            "Wednesday": "11:00 AM - 1:00 PM: OS Lecture"
        },
        "4cb23ai": {
            "Monday": "10:00 AM - 11:00 AM: Linear Algebra, 11:00 AM - 1:00 PM: AI Principles Lab",
            "Tuesday": "9:00 AM - 10:00 AM: Ethics in AI",
            "Wednesday": "1:00 PM - 2:00 PM: Communication Skills"
        }
    }
    
    for prefix, schedule_data in mock_schedules.items():
        try:
            schedule_json = json.dumps(schedule_data)
            cursor.execute(
                "INSERT INTO schedules (usn_prefix, schedule) VALUES (?, ?)", 
                (prefix, schedule_json)
            )
        except sqlite3.IntegrityError:
             pass
    
    conn.commit()
    conn.close()

# Run the initialization
init_db()

# --- Security Helpers ---
def get_db_connection():
    """Returns a connection to the database."""
    return sqlite3.connect(DB_NAME)

def hash_password(password: str) -> str:
    """Hash a password using the configured scheme."""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)

# --- Pydantic Models for Data Validation ---

ALLOWED_BRANCHES = ['CS', 'AI', 'CSBS', 'CSD', 'IS', 'EC']
ALLOWED_ROLES = ['student', 'faculty', 'placement_cell']
StudyYear = Literal[0, 1, 2, 3, 4] 

class UserRegistration(BaseModel):
    email: EmailStr
    full_name: str
    username: str = Field(min_length=4)
    branch: Literal[tuple(ALLOWED_BRANCHES)] 
    usn: str
    study_year: StudyYear
    role: Literal[tuple(ALLOWED_ROLES)]
    password: str = Field(min_length=8)

    @validator('usn', always=True)
    def validate_usn_format(cls, v, values):
        role = values.get('role')
        
        if role == 'student':
            # Student USN validation (e.g., 4cb23cs001)
            branch_map = {'CS': 'cs', 'AI': 'ai', 'CSBS': 'cb', 'CSD': 'cd', 'IS': 'is', 'EC': 'ec'}
            expected_branch_code = branch_map.get(values.get('branch').upper()) if values.get('branch') else None
            if not expected_branch_code:
                raise PydanticCustomError('usn_validation', 'Internal branch code error.')
            usn_pattern = re.compile(rf"^4cb(\d{{2}})({expected_branch_code})(\d{{3}})$", re.IGNORECASE)
            if not usn_pattern.match(v):
                raise PydanticCustomError(
                    'usn_validation', 
                    f'USN must be in the format 4cbYY[BranchCode]NNN, specific to the selected branch ({expected_branch_code}).'
                )
        elif role in ['faculty', 'placement_cell']:
            # Faculty/Placement Employee ID validation (10 digits)
            if not re.match(r"^\d{10}$", v):
                raise PydanticCustomError(
                    'id_validation', 
                    'Employee ID must be exactly 10 digits (MMyyIDNNNN).'
                )
            
        return v.lower()

    @validator('study_year', always=True)
    def validate_study_year_by_role(cls, v, values):
        role = values.get('role')
        if role == 'student':
            if not 1 <= v <= 4:
                raise PydanticCustomError('role_year_mismatch', 'Student study_year must be between 1 and 4.')
        elif role in ['faculty', 'placement_cell']:
            if v != 0:
                raise PydanticCustomError('role_year_mismatch', f'{role} must have a study_year of 0.')
        return v

class Login(BaseModel):
    email: str
    password: str

class ChatQuery(BaseModel):
    user_email: str
    query: str

class JobPost(BaseModel):
    title: str
    company: str
    description: str
    eligibility_branch: str
    application_link: str

# --- API Endpoints: Auth ---

@app.post("/register", status_code=status.HTTP_201_CREATED, tags=["Auth"])
def register_user(user_data: UserRegistration):
    hashed_password = hash_password(user_data.password)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO users (email, full_name, username, branch, usn, study_year, role, password_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_data.email,
            user_data.full_name,
            user_data.username,
            user_data.branch.upper(), 
            user_data.usn.lower(), 
            user_data.study_year,
            user_data.role.lower(),
            hashed_password
        ))
        conn.commit()
        return {"message": "Registration successful!", "username": user_data.username}
    except sqlite3.IntegrityError as e:
        detail = "Email, USN/Employee ID, or Username already exists."
        if "email" in str(e): detail = "Email already registered."
        elif "usn" in str(e): detail = "USN/Employee ID already registered."
        elif "username" in str(e): detail = "Username already taken."
        raise HTTPException(status_code=400, detail=detail)
    finally:
        conn.close()

@app.post("/login", tags=["Auth"])
def login_user(credentials: Login):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT role, study_year, password_hash FROM users WHERE email = ?", (credentials.email,))
    user = cursor.fetchone()
    conn.close()

    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    role, study_year, stored_hash = user

    if verify_password(credentials.password, stored_hash):
        return {
            "message": "Login successful", 
            "role": role, 
            "study_year": study_year,
            # Dashboard logic: 'student_placements' triggers both dashboards on frontend
            "dashboard": "student_placements" if role == "student" and study_year in [3, 4] else role
        }
    else:
        raise HTTPException(status_code=401, detail="Invalid email or password.")

# --- API Endpoints: Gemini AI ---

@app.post("/gemini-chat", tags=["AI"])
def get_gemini_response(chat_data: ChatQuery):
    if not gemini_client:
        raise HTTPException(status_code=503, detail="Gemini service not configured (check API Key).")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT branch, study_year, role FROM users WHERE email = ?", (chat_data.user_email,))
    user_context = cursor.fetchone()
    conn.close()
    
    if not user_context:
        raise HTTPException(status_code=404, detail="User not found.")
        
    branch, study_year, role = user_context
    
    context_text = f"User Role: {role.capitalize()}, Branch: {branch}, Year: {study_year}."
    
    system_prompt = (
        f"You are Clg GPT, a helpful AI assistant for a college in India. "
        f"Your user context is: {context_text}. Be professional and concise. "
        "If they ask for a PDF of notes, tell them to refer to the official Google Drive link shared by their faculty/college, and offer to explain concepts instead."
    )
    
    try:
        CHAT_CONFIG = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=0.4,
        )
        
        response = gemini_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=chat_data.query,
            config=CHAT_CONFIG
        )
        
        return {"response": response.text}
        
    except APIError as e:
        raise HTTPException(status_code=500, detail=f"Gemini API Error: {e}")

# --- API Endpoints: Placement Cell ---

@app.post("/placement/upload-job", status_code=status.HTTP_201_CREATED, tags=["Placement"])
def upload_job_post(job_data: JobPost, user_email: str):
    # This endpoint needs a security check in a real app to ensure user_email is placement_cell
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("""
            INSERT INTO jobs (title, company, description, eligibility_branch, application_link)
            VALUES (?, ?, ?, ?, ?)
        """, (
            job_data.title,
            job_data.company,
            job_data.description,
            job_data.eligibility_branch,
            job_data.application_link
        ))
        conn.commit()
        return {"message": f"Job Post for {job_data.company} uploaded successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.get("/placement/jobs", tags=["Placement"])
def get_all_job_posts():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT 
            id, title, company, description, eligibility_branch, application_link 
        FROM jobs
    """)
    
    jobs = []
    columns = [desc[0] for desc in cursor.description]
    
    for row in cursor.fetchall():
        jobs.append(dict(zip(columns, row)))
        
    conn.close()
    
    if not jobs:
        return {"message": "No job posts are currently available."}
        
    return {"jobs": jobs}

# --- API Endpoints: Student Utility ---

@app.get("/student/notes-link/{branch}", tags=["Student"])
def get_notes_link(branch: str):
    """Retrieves the dedicated Google Drive link for branch-specific academic notes."""
    
    branch_upper = branch.upper()
    
    if branch_upper in NOTES_LINK_MAP:
        return {
            "branch": branch_upper,
            "message": f"Official Google Drive link for {branch_upper} notes is provided below.",
            "notes_link": NOTES_LINK_MAP[branch_upper]
        }
    else:
        raise HTTPException(
            status_code=404, 
            detail=f"Notes link not found for branch: {branch_upper}. Please contact the department."
        )

@app.get("/student/schedule/{usn}", tags=["Student"])
def get_daily_schedule(usn: str):
    """Retrieves the daily timetable/schedule based on the USN prefix."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Use the USN prefix (e.g., '4cb23cs') to look up the schedule
    usn_prefix = usn[:7].lower()
    
    cursor.execute("SELECT schedule FROM schedules WHERE usn_prefix = ?", (usn_prefix,))
    schedule_row = cursor.fetchone()
    conn.close()

    if not schedule_row:
        raise HTTPException(
            status_code=404, 
            detail=f"Daily schedule not found for USN prefix: {usn_prefix}. The timetable may not be released yet."
        )
        
    # The stored schedule is a JSON string, load it back into a Python object
    schedule_data = json.loads(schedule_row[0])
    
    return {
        "usn_prefix": usn_prefix,
        "message": f"Daily schedule for batch {usn_prefix}:",
        "schedule": schedule_data
    }

# --- Run the application ---
# To run this, execute in your terminal:
# python -m uvicorn main:app --reload