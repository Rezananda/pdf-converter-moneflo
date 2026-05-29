from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
import fitz  # PyMuPDF
import pytz
from datetime import datetime, time
try:
    from api.parsers import parse_bank_statement, validate_bank_statement, detect_bank
except ModuleNotFoundError:
    from parsers import parse_bank_statement, validate_bank_statement, detect_bank
import os
import re
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

# =====================================================================
# SUPABASE CLIENTS CONFIGURATION
# =====================================================================

def get_supabase() -> Client:
    """Client standar (Anon). Digunakan hanya untuk verifikasi token JWT."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_KEY")

    if not url or not key:
        raise HTTPException(
            status_code=500,
            detail="Supabase environment variables not configured"
        )
    return create_client(url, key)

def get_service_supabase() -> Client:
    """Client Dewa (Service Role). Digunakan di backend untuk Bypass RLS Policy."""
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise HTTPException(
            status_code=500,
            detail="SUPABASE_SERVICE_ROLE_KEY belum dipasang di file .env"
        )
    return create_client(url, key)

# =====================================================================
# AUTH & LIMIT VALIDATION
# =====================================================================

async def verify_token(
    authorization: str = Header(None),
    supabase: Client = Depends(get_supabase)
):
    """Mengecek JWT Token dari Frontend."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    
    token = authorization.split(" ")[1]
    try:
        user_response = supabase.auth.get_user(token)
        if user_response and hasattr(user_response, 'user'):
            return user_response.user
        return None
    except Exception as e:
        print(f"DEBUG: Token verification failed: {e}")
        return None

async def check_upload_limit(user_obj):
    """Memvalidasi limit upload harian user (Anonim/Terdaftar) via Service Role."""
    is_registered = False
    user_id = None

    # 1. Identifikasi User Tier
    if user_obj:
        user_id = user_obj.id
        app_metadata = getattr(user_obj, 'app_metadata', {})
        is_anon_provider = app_metadata.get("provider") == "anonymous" or getattr(user_obj, 'is_anonymous', False)
        
        if not is_anon_provider:
            is_registered = True

    config_key = "upload_limit_daily_registered" if is_registered else "upload_limit_daily"
    tier_name = "Terdaftar" if is_registered else "Anonim"

    # Panggil Client Bypass RLS
    admin_db = get_service_supabase()

    # 2. Ambil Limit dari DB
    try:
        config_res = admin_db.table("app_config").select("value, is_active").eq("key", config_key).execute()
        if config_res.data and config_res.data[0].get("is_active") != False:
            max_limit = int(config_res.data[0].get("value"))
        else:
            raise HTTPException(status_code=500, detail=f"Konfigurasi limit {tier_name} tidak aktif.")
    except HTTPException:
        raise
    except Exception as e:
        print(f"ERROR: Gagal membaca app_config: {e}")
        raise HTTPException(status_code=500, detail="Gagal memproses limit dari server.")

    # 3. Set Boundary Waktu Hari Ini (Sync dengan FE)
    tz = pytz.timezone('Asia/Jakarta')
    now_local = datetime.now(tz)
    start_local = datetime.combine(now_local.date(), time.min)
    end_local = datetime.combine(now_local.date(), time.max)

    start_bound = tz.localize(start_local).astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    end_bound = tz.localize(end_local).astimezone(pytz.utc).strftime('%Y-%m-%dT%H:%M:%S.999Z')

    # 4. Hitung Upload Harian
    try:
        query = (
            admin_db.table("upload_logs")
            .select("*", count="exact")
            .gte("created_at", start_bound)
            .lte("created_at", end_bound)
        )
        
        if is_registered:
            query = query.eq("is_registered", True).eq("user_id", user_id)
        else:
            query = query.eq("is_registered", False)
            if user_id:
                query = query.or_(f"user_id.eq.{user_id},user_id.is.null")
            
        logs_res = query.execute()
        current_count = logs_res.count if logs_res.count is not None else len(logs_res.data)
        
        print(f"=== [MONEFLO FILTER LOG] ===")
        print(f"Tier User    : {tier_name}")
        print(f"Total Di DB  : {current_count} / {max_limit}")
        print(f"============================")

    except Exception as e:
        print(f"ERROR: Gagal verifikasi penggunaan: {e}")
        raise HTTPException(status_code=500, detail="Gagal verifikasi kuota harian.")

    # 5. Eksekusi Pemblokiran
    if current_count >= max_limit:
        raise HTTPException(
            status_code=429, 
            detail=f"Batas upload harian tercapai. Sebagai user {tier_name}, Anda diperbolehkan maksimal {max_limit} file per hari."
        )

# =====================================================================
# UTILITIES
# =====================================================================

async def save_unknown_statement(filename: str, raw_text: str, metadata: dict, user_id: str = None):
    """Menyimpan statement tidak dikenali menggunakan Service Client."""
    try:
        supabase = get_service_supabase()
        
        hint_patterns = [
            r"Bank\s+\w+", r"PT\.?\s*Bank\s+\w+",
            r"BRI|BCA|BNI|Mandiri|CIMB|Danamon|BTN|Permata|OCBC|BSI|Jenius|Allo|Neo\s*Bank"
        ]
        hints = list(dict.fromkeys([h.strip() for pat in hint_patterns for h in re.findall(pat, raw_text, re.IGNORECASE)[:3]]))[:10]

        record = {
            "filename": filename,
            "raw_text": raw_text[:50000],
            "pdf_metadata": metadata or {},
            "detected_hints": hints,
            "status": "pending",
        }
        if user_id: record["submitted_by"] = user_id

        supabase.table("unknown_bank_statements").insert(record).execute()
        print(f"INFO: Unknown statement saved: '{filename}'")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed saving unknown statement: {e}")


def categorize_transaction(description: str) -> str:
    desc = description.lower()
    mapping = {
        'Food & Beverage': ['restaurant', 'cafe', 'kopi', 'coffee', 'food', 'drink', 'mcdonald', 'burger', 'pizza', 'kfc', 'ayam', 'nasi', 'bakso', 'sate', 'martabak', 'warung', 'jajan', 'kuliner', 'gorengan', 'telor gulu', 'ayojajan', 'bubur', 'roti o', 'roti\'o', 'snack', 'tahu'],
        'Transportation': ['taxi', 'uber', 'grab', 'bus', 'train', 'metro', 'fuel', 'bensin', 'pertamina', 'tol', 'parking', 'angkutan', 'ojek'],
        'Shopping': ['tokopedia', 'shopee', 'lazada', 'mall', 'store', 'shop', 'grosir', 'supermarket', 'indomaret', 'alfamart', 'e-commerce', 'purchase', 'familymart', 'indoma', 'market', 'idm', 'qris livin', 'edc'],
        'Entertainment': ['cinema', 'movie', 'theatre', 'concert', 'ticket', 'spotify', 'netflix', 'gaming', 'hallo', 'event', 'floating market'],
        'Utilities': ['pln', 'electric', 'listrik', 'water', 'gas', 'telekom', 'internet', 'telkom', 'pdam', 'utility', 'indihome', 'finnet', 'prepaid'],
        'Rent & Housing': ['rent', 'sewa', 'kos', 'apartemen', 'indekos', 'housing', 'property'],
        'Medical & Health': ['apotek', 'pharmacy', 'clinic', 'hospital', 'dokter', 'medicine', 'health', 'obat', 'suplemen', 'apotik'],
        'Education': ['school', 'university', 'college', 'course', 'kelas', 'tuition', 'education', 'pelatihan', 'bimbel'],
        'Transaction': ['transfer', 'payment', 'withdraw', 'setoran', 'deposit', 'pembayaran', 'flazz', 'e-money', 'tapcash', 'biaya admin', 'biaya', 'admin', 'atm', 'trsf', 'bi-fast', 'ftfva', 'ftscy', 'kartu kredit', 'tunai'],
    }
    for category, keywords in mapping.items():
        if any(kw in desc for kw in keywords): return category
    return 'Other'

# =====================================================================
# API ENDPOINTS
# =====================================================================

@app.get("/")
def home():
    return {"message": "PDF Converter API is Running!"}

@app.post("/api/v1/convert")
async def convert_pdf_to_text(
    file: UploadFile = File(...), 
    password: str = Form(None), 
    user: dict = Depends(verify_token)
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="File yang dikirim bukan PDF")

    # 🔒 PENGECEKAN LIMIT EKSEKUSI (WAJIB AWAIT)
    await check_upload_limit(user)

    try:
        file_content = await file.read()
        
        if not file_content.startswith(b"%PDF-"):
             raise HTTPException(status_code=400, detail="File yang dikirim bukan PDF")

        try:
            doc = fitz.open(stream=file_content, filetype="pdf")
        except Exception:
             raise HTTPException(status_code=400, detail="File yang dikirim bukan PDF")

        if doc.needs_pass:
            if not password:
                raise HTTPException(status_code=400, detail="PDF dilindungi password.")
            if not doc.authenticate(password):
                raise HTTPException(status_code=400, detail="Password salah, coba lagi.")

        text_output = ""
        for page in doc:
            text_output += page.get_text("text", sort=True) + "\n"
            
        if not validate_bank_statement(text_output):
             raise HTTPException(status_code=400, detail="File bukan merupakan bank statement valid.")

        bank = detect_bank(text_output, doc.metadata)
        
        if bank == "UNKNOWN":
            user_id = str(user.id) if user else None
            await save_unknown_statement(file.filename, text_output, doc.metadata, user_id)
            
            return {
                "period": "", "initial_balance": 0.0, "closing_balance": 0.0,
                "incoming_transactions": 0.0, "outgoing_transactions": 0.0,
                "transactions": [], "filename": file.filename, "is_smart_parsed": True,
                "status": "unknown_bank",
                "message": "Format bank statement ini belum dikenali oleh sistem."
            }

        try:
            result = parse_bank_statement(text_output, doc.metadata, file.filename)
            result["filename"] = file.filename
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Parsing error: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")


@app.post("/api/v1/categorize")
async def categorize_transactions(payload: dict, user: dict = Depends(verify_token)):
    result = dict(payload)
    transactions = result.get('transactions', [])
    for trx in transactions:
        if trx.get('amount_type') == 'debit':
            trx['transaction_label'] = categorize_transaction(trx.get('transaction_description', ''))
        else:
            trx['transaction_label'] = ''
    result['transactions'] = transactions
    return result

if __name__ == "__main__":
    import sys, uvicorn
    api_dir = os.path.dirname(os.path.abspath(__file__))
    if api_dir not in sys.path: sys.path.insert(0, api_dir)
    uvicorn.run("index:app", host="0.0.0.0", port=8000, reload=True)