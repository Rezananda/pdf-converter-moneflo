from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
from api.parsers import parse_bank_statement, validate_bank_statement, detect_bank
import os
import re
import traceback
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

def get_supabase() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise HTTPException(
            status_code=500,
            detail="Supabase environment variables not configured"
        )

    return create_client(url, key)

# Caching for the system authenticated client
_system_supabase_client = None

async def get_system_supabase() -> Client:
    """
    Returns a Supabase client authenticated as the system API user.
    Uses credentials from SUPABASE_SYSTEM_EMAIL and SUPABASE_SYSTEM_PASSWORD.
    """
    global _system_supabase_client
    
    if _system_supabase_client:
        return _system_supabase_client
        
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")
    email = os.environ.get("SUPABASE_SYSTEM_EMAIL")
    password = os.environ.get("SUPABASE_SYSTEM_PASSWORD")
    
    if not all([url, key, email, password]):
        print("WARNING: System credentials not fully configured in .env. Falling back to anon.")
        return create_client(url, key)

    try:
        client = create_client(url, key)
        client.auth.sign_in_with_password({"email": email, "password": password})
        _system_supabase_client = client
        print(f"INFO: System API client authenticated successfully as {email}")
        return _system_supabase_client
    except Exception as e:
        print(f"ERROR: System API authentication failed: {e}. Falling back to anon.")
        return create_client(url, key)

async def verify_token(
    authorization: str = Header(None),
    supabase: Client = Depends(get_supabase)
):
    """
    Verifies the Supabase JWT token if present.
    If missing, returns None to allow guest access.
    """
    if not authorization:
        return None

    if not authorization.startswith("Bearer "):
        # If it's a guest who accidentally sent a malformed header, still allow access
        return None
    
    token = authorization.split(" ")[1]
    
    try:
        # verify the token with Supabase
        user_response = supabase.auth.get_user(token)
        if user_response and hasattr(user_response, 'user'):
            return user_response
        return None
    except Exception as e:
        print(f"DEBUG: Token verification failed: {e}")
        return None

async def save_unknown_statement(
    filename: str,
    raw_text: str,
    metadata: dict,
    user_id: str = None
):
    """
    Saves an unrecognized bank statement raw text to Supabase
    so developers can study it and build a specific parser later.
    Includes metadata and detected hints for better analysis.
    Uses the authenticated system client to bypass RLS restrictions.
    """
    try:
        # Get the authenticated system client
        supabase = await get_system_supabase()
        
        # Extract potential hints from the text (bank name candidates)
        hint_patterns = [
            r"Bank\s+\w+", r"PT\.?\s*Bank\s+\w+",
            r"BRI|BCA|BNI|Mandiri|CIMB|Danamon|BTN|Permata|OCBC|BSI|Jenius|Allo|Neo\s*Bank"
        ]
        hints = []
        for pat in hint_patterns:
            found = re.findall(pat, raw_text, re.IGNORECASE)
            hints.extend([h.strip() for h in found[:3]])  # max 3 per pattern
        # Deduplicate
        hints = list(dict.fromkeys(hints))[:10]

        record = {
            "filename": filename,
            "raw_text": raw_text[:50000],   # Limit to 50k chars to avoid DB overflow
            "pdf_metadata": metadata or {},
            "detected_hints": hints,
            "status": "pending",
        }
        if user_id:
            record["submitted_by"] = user_id

        # Perform the insert. We use the system account to satisfy RLS requirements.
        print(f"DEBUG: Attempting to insert unknown statement to Supabase: {filename}")
        response = supabase.table("unknown_bank_statements").insert(record).execute()
        
        # Log result
        if hasattr(response, 'data') and response.data:
            print(f"INFO: Successfully saved unknown bank statement '{filename}' (ID: {response.data[0].get('id', 'N/A')}).")
        else:
            print(f"WARNING: Supabase insert completed but returned no data for '{filename}'. Response: {response}")

    except Exception as e:
        # Non-critical: do not crash the main request if saving fails
        print(f"CRITICAL ERROR: Could not save unknown statement to Supabase for '{filename}':")
        print(traceback.format_exc())


@app.get("/")
def home():
    return {"message": "PDF Converter API is Running!"}

@app.post("/api/v1/convert")
async def convert_pdf_to_text(
    file: UploadFile = File(...), 
    password: str = Form(None), 
    user: dict = Depends(verify_token)
):
    # 1. Validate File Extension
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="file yang dikirim bukan pdf")

    supabase = get_supabase()

    try:
        # Read file content into memory
        file_content = await file.read()
        
        # 2a. Robust PDF Validation: Check Magic Number
        if not file_content.startswith(b"%PDF-"):
             raise HTTPException(status_code=400, detail="file yang dikirim bukan pdf")

        # Open PDF using PyMuPDF
        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
        except Exception:
             raise HTTPException(status_code=400, detail="file yang dikirim bukan pdf")

        # 2b. Check if PDF needs a password
        if doc.needs_pass:
            if not password:
                raise HTTPException(
                    status_code=400, 
                    detail="This PDF is password protected. Please provide a password."
                )
            if not doc.authenticate(password):
                raise HTTPException(
                    status_code=400, 
                    detail="incorrect password, please retry again"
                )

        text_output = ""
        for page in doc:
            text_output += page.get_text("text", sort=True) + "\n"
            
        # 2c. Bank Statement Validation: Content Check
        if not validate_bank_statement(text_output):
             raise HTTPException(
                 status_code=400, 
                 detail="file yang dikirim bukan merupakan bank statement yang valid"
             )

        # 3. Detect bank before parsing
        bank = detect_bank(text_output, doc.metadata)
        
        # 4. If bank is UNKNOWN: save to Supabase for learning, return raw text response
        if bank == "UNKNOWN":
            print(f"INFO: Unknown bank statement '{file.filename}'. Saving to Supabase.")
            
            # Extract user ID from the verified user object
            user_id = None
            try:
                user_id = str(user.user.id) if hasattr(user, 'user') else None
            except Exception:
                pass
            
            await save_unknown_statement(
                filename=file.filename,
                raw_text=text_output,
                metadata=doc.metadata,
                user_id=user_id
            )
            
            # Return a structured response indicating it was saved for learning
            return {
                "period": "",
                "initial_balance": 0.0,
                "closing_balance": 0.0,
                "incoming_transactions": 0.0,
                "outgoing_transactions": 0.0,
                "transactions": [],
                "filename": file.filename,
                "is_smart_parsed": True,
                "status": "unknown_bank",
                "message": (
                    "Format bank statement ini belum dikenali oleh sistem. "
                    "Akan didukung pada pembaruan berikutnya."
                )
            }

        # 5. Parse with specific parser
        try:
            result = parse_bank_statement(text_output, doc.metadata, file.filename)
            result["filename"] = file.filename
            return result
        except Exception as e:
            traceback.print_exc()
            raise HTTPException(status_code=400, detail=f"Gagal melakukan konversi: {str(e)}")

    except HTTPException as http_exc:
        raise http_exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {repr(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.index:app", host="0.0.0.0", port=8000, reload=True)