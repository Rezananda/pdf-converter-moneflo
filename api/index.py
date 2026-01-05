from fastapi import FastAPI, UploadFile, File, Form, HTTPException
import fitz  # PyMuPDF

app = FastAPI()

@app.get("/")
def home():
    return {"message": "PDF Converter API is Running!"}

@app.post("/api/convert")
async def convert_pdf_to_text(
    file: UploadFile = File(...), 
    password: str = Form(None) # 1. Accept optional password field
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
            text_output += page.get_text() + "\n"
            
        return {
            "filename": file.filename,
            "page_count": len(doc),
            "is_encrypted": doc.is_encrypted,
            "text_preview": text_output[:100] + "...", 
            "full_text": text_output
        }

    except HTTPException as http_exc:
        # Re-raise custom HTTP exceptions (like 400 or 401)
        raise http_exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")