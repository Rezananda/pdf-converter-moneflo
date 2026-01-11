from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
from api.parsers import parse_bank_statement
import os
from supabase import create_client, Client
from dotenv import load_dotenv

# Load env vars from .env file if present
load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",  # local dev
        "https://moneflo.netlify.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Supabase Client
# url: str = os.environ.get("SUPABASE_URL")
# key: str = os.environ.get("SUPABASE_KEY")
# supabase: Client = create_client(url, key) if url and key else None

def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise HTTPException(
            status_code=500,
            detail="Supabase environment variables not configured"
        )

    return create_client(url, key)

async def verify_token(
    authorization: str = Header(...),
    supabase: Client = Depends(get_supabase)
):
    if not supabase:
        # If supabase is not configured, strictly speaking we should probably fail safe
        # But for dev maybe we log a warning?
        # User requirement says: "if not valid then throw error".
        # If env vars missing => we can't validate => fail.
        raise HTTPException(status_code=500, detail="Server authentication not configured")

    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid token format")
    
    token = authorization.split(" ")[1]
    
    try:
        user = supabase.auth.get_user(token)
        if not user:
             raise HTTPException(status_code=401, detail="user unauthorized")
        return user
    except Exception as e:
        # Supabase raises exception on invalid token
        raise HTTPException(status_code=401, detail="user unauthorized")

@app.get("/")
def home():
    return {"message": "PDF Converter API is Running!"}

@app.post("/api/v1/convert")
async def convert_pdf_to_text(
    file: UploadFile = File(...), 
    password: str = Form(None), # 1. Accept optional password field
    user: dict = Depends(verify_token) # 2. Validate token
):
    # Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF")

    try:
        # Read file content into memory
        file_content = await file.read()
        
        # Open PDF using PyMuPDF
        doc = fitz.open(stream=file_content, filetype="pdf")

        # 2. Check if PDF needs a password
        if doc.needs_pass:
            if not password:
                # Case: PDF is locked, but NO password was sent
                raise HTTPException(
                    status_code=400, 
                    detail="This PDF is password protected. Please provide a password."
                )
            
            # 3. Try to unlock with the provided password
            # authenticate returns True if success, False if fail
            if not doc.authenticate(password):
                 # Case: PDF is locked, but WRONG password was sent
                raise HTTPException(
                    status_code=401, 
                    detail="Incorrect password provided."
                )

        text_output = ""
        
        # Extract text
        for page in doc:
            # sort=True attempts to order text by physical position (reading order)
            text_output += page.get_text("text", sort=True) + "\n"
            
        # Parse Bank Statement
        try:
            # We pass text_output, metadata, and filename to the parser
            result = parse_bank_statement(text_output, doc.metadata, file.filename)
            return result
        except ValueError as e:
            # "Bank Not Supported" error
            raise HTTPException(status_code=400, detail=str(e))

    except HTTPException as http_exc:
        # Re-raise custom HTTP exceptions (like 400 or 401)
        raise http_exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {repr(e)}")