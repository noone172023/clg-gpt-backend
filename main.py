import os
import uvicorn
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Any
from passlib.context import CryptContext
from dotenv import load_dotenv
from google import genai
from google.genai import types

# Assuming you have committed database.py which contains the DB class
from database import DB 


# Load environment variables (like GEMINI_API_KEY)
load_dotenv()

# --- Pydantic Schemas (Defining them here since schemas.py is missing) ---

class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    username: str
    branch: str = Field(pattern=r'^(CS|AI|IS)$')
    usn: str = Field(min_length=10, max_length=10) # USN or Employee ID
    study_year: int = Field(ge=1, le=4)
    role: str = Field(pattern=r'^(student|faculty|placement_cell)$')
    
class Login(BaseModel):
    email: EmailStr
    password: str

class ChatQuery(BaseModel):
    user_email: EmailStr
    query: str


# --- Backend Configuration ---
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
db = DB()

app = FastAPI(
    title="CLGPT Backend API",
    description="Backend service for CLGPT using FastAPI, SQLite, and the Gemini API.",
    version="1.0.0"
)

# Define the list of allowed registration/login emails (Whitelist)
ALLOWED_EMAILS = {
    "shreyashetty670@gmail.com", 
    "swathi6105@gmail.com", 
    "thrisha745@gmail.com"
}


# --- Utility Functions ---

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def determine_user_dashboard(role: str, study_year: int) -> str:
    """Determines which dashboard the user should see."""
    if role == "student" and study_year in [3, 4]:
        return "student_placements"
    if role == "student":
        return "student_general"
    # Placeholder for other roles
    return "general"
    
# --- Gemini Handler Functions (Defining them here since gemini_handler.py is missing) ---

def generate_response(prompt: str, system_instruction: str) -> str:
    """Sends a query to the Gemini API and returns the text response."""
    
    # 1. Initialize the client using the environment variable
    # Reads the GEMINI_API_KEY set in Render environment variables
    client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    
    # 2. Configure the model with the user's context
    config = types.GenerateContentConfig(
        system_instruction=system_instruction
    )
    
    # 3. Call the API
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt],
        config=config,
    )
    
    return response.text


# --- API Endpoints ---

@app.get("/")
async def root():
    return {"message": "Welcome to the CLGPT Backend API. Use the /register, /login, or /gemini-chat endpoints."}

@app.post("/register", status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate):
    # 1. Email Whitelist Check
    if user_data.email.lower() not in ALLOWED_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration denied. This email is not on the allowed list."
        )

    # 2. Check for existing user
    if db.find_user_by_email(user_data.email):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User with this email already exists."
        )

    # 3. Handle ID field based on role (USN for students, Employee ID for others)
    usn = user_data.usn
    # Note: Pydantic validation handles the length now, but keeping this for role check
    if user_data.role != 'student' and len(usn) != 10:
         raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Faculty/Placement Cell Employee ID must be 10 digits."
        )

    # 4. Hash Password and create user object
    hashed_password = get_password_hash(user_data.password)
    user_dict = user_data.model_dump()
    user_dict["hashed_password"] = hashed_password
    del user_dict["password"] 

    # 5. Save to Database
    db.create_user(user_dict)
    
    return {"message": "User registered successfully", "email": user_data.email}

@app.post("/login")
async def login(login_data: Login):
    # 1. Email Whitelist Check
    if login_data.email.lower() not in ALLOWED_EMAILS:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Login denied. This email is not associated with an authorized user."
        )

    # 2. Retrieve user
    user = db.find_user_by_email(login_data.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )
    
    # 3. Verify password
    if not verify_password(login_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials"
        )

    # 4. Success: Prepare response data
    dashboard = determine_user_dashboard(user['role'], user.get('study_year', 0))

    return {
        "message": "Login successful",
        "email": user["email"],
        "role": user["role"],
        "study_year": user.get('study_year', 0),
        "dashboard": dashboard
    }

@app.post("/gemini-chat")
async def chat_with_gemini(query: ChatQuery):
    # 1. Retrieve user context
    user = db.find_user_by_email(query.user_email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Please log in again."
        )

    # 2. Create context string for RAG
    user_context = (
        f"User Role: {user['role']}, "
        f"Branch: {user['branch']}, "
        f"Study Year: {user.get('study_year', 'N/A')}. "
        "Base all technical responses on this context."
    )
    
    # 3. Generate response using Gemini
    try:
        response_text = generate_response(
            prompt=query.query,
            system_instruction=user_context
        )
        # 4. Store chat history (optional, depending on your DB implementation)
        # db.log_chat_message(query.user_email, query.query, response_text)

        return {"query": query.query, "response": response_text}

    except Exception as e:
        print(f"Gemini API Error: {e}")
        # Note: If API key is wrong, the client initialization will fail here.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"An error occurred while processing the request: {e}"
        )

# --- Student Utility Endpoints ---

@app.get("/student/notes-link/{branch}")
async def get_notes_link(branch: str):
    # Simple mock data based on branch
    links = {
        "CS": "https://drive.google.com/drive/folders/CS_Notes_2025",
        "AI": "https://drive.google.com/drive/folders/AI_Notes_2025",
        "IS": "https://drive.google.com/drive/folders/IS_Notes_2025",
    }
    link = links.get(branch.upper())
    if not link:
        raise HTTPException(status_code=404, detail="Notes link not found for this branch.")
    return {"message": f"Notes link for {branch.upper()}", "link": link}

@app.get("/student/schedule/{usn}")
async def get_schedule(usn: str):
    # Simple mock data based on USN year
    # Assumes USN format like 4cb23cs001 where '23' implies the year
    try:
        year_code = usn[3:5]
        if year_code == '23':
            schedule_link = "https://calendar.google.com/calendar/u/0/23_Batch_Schedule"
        elif year_code == '22':
            schedule_link = "https://calendar.google.com/calendar/u/0/22_Batch_Schedule"
        else:
            schedule_link = None
            
        if not schedule_link:
            raise HTTPException(status_code=404, detail=f"Schedule link not found for USN starting with year code '{year_code}'.")
            
        return {"message": f"Schedule link for USN {usn}", "link": schedule_link}
    except IndexError:
        raise HTTPException(status_code=400, detail="Invalid USN format.")

# --- Placement Utility Endpoints ---

@app.get("/placement/jobs")
async def get_job_posts():
    # Mock job post data
    jobs = [
        {"title": "Software Engineer Intern", "company": "TechCorp", "salary": "₹50k/month", "role_type": "IT"},
        {"title": "Data Analyst Trainee", "company": "AnalyticsPro", "salary": "₹45k/month", "role_type": "IT"},
        {"title": "Core Research Fellow", "company": "PureChem", "salary": "₹60k/month", "role_type": "Non-IT"},
    ]
    return {"message": "Current available job posts for eligible students.", "jobs": jobs}

# The main function to run the application (typically kept at the end)
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)