import re
from datetime import datetime

def parse_bank_statement(text: str, metadata: dict) -> list[dict]:
    """
    Identifies the bank based on metadata and parses the text into structured transaction data.
    """
    creator = metadata.get("creator", "")
    
    if "E-statement Batch Generator (PT. Bank Central Asia, Tbk)" in creator:
        return parse_bca(text)
    elif "PT. Bank Mandiri (Persero) Tbk" in creator:
        return parse_mandiri(text)
    else:
        raise ValueError("Bank Not Supported")

def parse_bca(text: str) -> list[dict]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    transactions = []
    
    # 1. EXTRACT YEAR (Default to current if not found)
    current_year = str(datetime.now().year)
    period_match = re.search(r"PERIODE\s*:\s*[A-Z]+\s*(\d{4})", text, re.IGNORECASE)
    if period_match:
        current_year = period_match.group(1)
        
    current_trans = None
    description_buffer = []

    for i, line in enumerate(lines):
        # 0. SAFETY: SKIP SUMMARY / BALANCE LINES
        if re.search(r"SALDO AWAL|SALDO AKHIR|MUTASI CR|MUTASI DB", line, re.IGNORECASE):
            current_trans = None
            continue

        # 1. DETECT DATE (Start of Transaction)
        # Matches "01/11" or "25/11"
        date_match = re.match(r"^(\d{2})\/(\d{2})$", line)
        if date_match:
            current_trans = {
                "day": date_match.group(1),
                "month": date_match.group(2),
                "year": current_year
            }
            description_buffer = []
            continue

        # 2. DETECT AMOUNT (Strict Format)
        # Regex: Digits 1-3, optional thousands groups, dot decimal. Optional DB marker.
        # Python Regex note: \d{1,3}(,\d{3})*\.\d{2} matches 1,000.00
        amount_match = re.match(r"^(\d{1,3}(?:,\d{3})*\.\d{2})\s*(DB)?$", line)

        if amount_match and current_trans:
            raw_amount = amount_match.group(1)
            db_marker = amount_match.group(2)

            amount_type = "debit" if db_marker == "DB" else "credit"
            
            # Clean Amount
            clean_amount = float(raw_amount.replace(",", ""))

            # Clean Description
            full_desc = " ".join(description_buffer)
            # Python string replace is literal, use re.sub for case insensitive text removal
            clean_desc = re.sub(r"TRANSAKSI DEBIT|TRANSAKSI KREDIT", "", full_desc, flags=re.IGNORECASE)
            clean_desc = re.sub(r"TGL:\s*\d{2}\/\d{2}", "", clean_desc, flags=re.IGNORECASE)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

            transactions.append({
                "transaction_date": f"{current_trans['year']}-{current_trans['month']}-{current_trans['day']}",
                "transaction_description": clean_desc if clean_desc else "BCA Transaction",
                "transaction_amount": clean_amount,
                "amount_type": amount_type,
                "transaction_bank": "BCA"
            })
            
            current_trans = None
            description_buffer = []
            continue

        # 3. COLLECT DESCRIPTION
        if current_trans:
            if not re.search(r"TANGGAL :", line):
                description_buffer.append(line)

    return transactions

def parse_mandiri(text: str) -> list[dict]:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    transactions = []

    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Mei": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }

    current_amount = None
    current_amount_type = None
    description_buffer = []

    for i, line in enumerate(lines):
        # 1. DETECT AMOUNT (e.g., "-50.000,00" or "+1.000.000,00")
        # Matches strictly: Start -> +/- -> Digits/Dots -> Comma -> 2 Digits -> End
        amount_match = re.match(r"^([+-])([\d.]+,[\d]{2})$", line)

        if amount_match:
            sign = amount_match.group(1)
            raw_val = amount_match.group(2)

            current_amount_type = "debit" if sign == "-" else "credit"
            # Mandiri: 1.000.000,00 -> remove dots, replace comma with dot -> 1000000.00
            current_amount = float(raw_val.replace(".", "").replace(",", "."))
            
            # START LOOKBACK to find Description
            description_buffer = []
            
            # Look back up to 6 lines
            for back in range(1, 7):
                if i - back < 0:
                    break
                prev_line = lines[i - back]
                
                # --- FILTERS ---
                
                # 1. Ignore Counter (digits only)
                if re.match(r"^\d+$", prev_line):
                    continue
                
                # 2. Ignore Balance (amount-like but no sign e.g. "166.600,72")
                if re.match(r"^[\d.]+,[\d]{2}$", prev_line):
                    continue
                
                # 3. Ignore Headers
                if re.search(r"Saldo|Balance|Nominal|Amount|Keterangan|Remarks", prev_line, re.IGNORECASE):
                    continue
                
                # 4. Ignore Time
                if re.search(r"\d{2}:\d{2}:\d{2}\sWIB", prev_line, re.IGNORECASE):
                    continue
                    
                # 5. Ignore Date (e.g. "01 Nov 2025") -> Stops lookback
                if re.search(r"\d{2}\s(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s\d{4}", prev_line, re.IGNORECASE):
                    break
                
                # 6. Ignore Page Numbers
                if re.search(r"^\d+\s(of|dari)\s\d+$", prev_line, re.IGNORECASE):
                    continue
                
                # Valid description
                description_buffer.insert(0, prev_line)
            
            continue

        # 2. DETECT DATE
        date_match = re.match(r"^(\d{2})\s(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s(\d{4})$", line, re.IGNORECASE)
        
        if date_match and current_amount is not None:
            day = date_match.group(1)
            month_str = date_match.group(2).capitalize() # Ensure title case for map
            # Handle Mei/May discrepancy if needed, but map covers multiple keys
            # The JS code had "Mei": "05" and "May": "05"
            # Capitalize might make "mei" -> "Mei", "MAY" -> "May"
            # Current map keys are Title Case (Jan, Feb...).
            month = month_map.get(month_str)
            # If map lookup fails (e.g. if regex matched something else), might default to original logic needed?
            # Regex ensures it matches one of the group options.
            # But the group options are hardcoded in regex "Jan|Feb...".
            # Note: the JS regex was case insensitive (/i), Python needs re.IGNORECASE.
            # So "JAN" matches. map needs to handle or we standardize.
            # Helper to standardize:
            if not month: 
                # Try title case logic again or direct lookup
                month = month_map.get(month_str.title(), "00")

            year = date_match.group(3)

            clean_desc = " ".join(description_buffer)
            clean_desc = re.sub(r"\s+", " ", clean_desc).strip()

            transactions.append({
                "transaction_date": f"{year}-{month}-{day}",
                "transaction_description": clean_desc if clean_desc else "Mandiri Transaction",
                "transaction_amount": current_amount,
                "amount_type": current_amount_type,
                "transaction_bank": "MANDIRI"
            })

            # Reset logic
            current_amount = None
            current_amount_type = None
            description_buffer = []

    return transactions
