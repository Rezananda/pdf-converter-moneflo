from fastapi import FastAPI, UploadFile, File, HTTPException
import fitz  # PyMuPDF
import io

app = FastAPI()

@app.get("/")
def home():
    return {"message": "PDF Converter API is Running!"}

@app.post("/api/convert")
async def convert_pdf_to_text(file: UploadFile = File(...)):
    # 1. Validate file type
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="File must be a PDF")

    try:
        # 2. Read file content into memory
        file_content = await file.read()
        
        # 3. Open PDF using PyMuPDF (from memory bytes)
        # We wrap bytes in io.BytesIO so fitz can read it like a file
        with fitz.open(stream=file_content, filetype="pdf") as doc:
            text_output = ""
            # 4. Extract text
            for page in doc:
                text_output += page.get_text() + "\n"
                
            return {
                "filename": file.filename,
                "page_count": len(doc),
                "text_preview": text_output[:100] + "...", # Show first 100 chars
                "full_text": text_output
            }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing PDF: {str(e)}")