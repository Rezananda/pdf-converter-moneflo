import re
from datetime import datetime

def parse_bank_statement(text: str, metadata: dict, filename: str = "") -> dict:
    creator = metadata.get("creator", "")
    print(f"DEBUG: parse_bank_statement called. Creator: '{creator}'")
    
    result = {}
    
    # Priority 1: Metadata Signature
    if "Bank Mandiri" in creator:
        return parse_mandiri(text)
    if "E-statement Batch Generator" in creator or "BCA" in creator.upper():
        return parse_bca(text)
    if "BNI" in creator.upper() or "Bank Negara Indonesia" in creator:
        return parse_bni(text)
    if "bluAccount" in text or "BCA Digital" in text or "bluSaving" in text:
        return parse_blu(text)
        
    # Priority 2: Specific Content Signature
    if "Tabungan NOW" in text or "Bank Mandiri" in text or "Mandiri Call" in text:
        return parse_mandiri(text)
        
    if "MUTASI REKENING" in text and "BCA" in text:
        return parse_bca(text)

    if "TAPLUS" in text and "BNI" in text:
        return parse_bni(text)
        
    # Fallback
    if "mandiri" in text.lower():
        return parse_mandiri(text)
    if "BCA" in text: # Weak fallback
        return parse_bca(text)
    
    raise ValueError("Bank Not Supported")

def clean_amount(amount_str: str) -> float:
    if not amount_str: 
        return 0.0
    
    # Keep digits, dots, commas, minus
    clean_str = re.sub(r"[^\d.,-]", "", str(amount_str))
    
    if not clean_str: 
        return 0.0

    # Handle negative sign usually at start or end
    is_negative = False
    if "-" in clean_str:
        is_negative = True
        clean_str = clean_str.replace("-", "")

    # Normalize Indonesian/EU format: 1.000,00 -> 1000.00
    # or US/BCA format: 1,000.00 -> 1000.00
    
    if "." in clean_str and "," in clean_str:
        if clean_str.find(".") < clean_str.find(","):
            # Dot first (thousands), Comma second (decimal) -> Mandiri
            val = clean_str.replace(".", "").replace(",", ".")
            return float(val) * (-1 if is_negative else 1)
        else:
            # Comma first (thousands), Dot second (decimal) -> BCA
            val = clean_str.replace(",", "")
            return float(val) * (-1 if is_negative else 1)
    elif "," in clean_str:
        # Check if comma is decimal (e.g. ,00 at end)
        if re.search(r",\d{2}$", clean_str):
             val = clean_str.replace(",", ".")
             return float(val) * (-1 if is_negative else 1)
        else:
             # Assume comma is thousands
             val = clean_str.replace(",", "")
             return float(val) * (-1 if is_negative else 1)
    
    # Simple number
    return float(clean_str) * (-1 if is_negative else 1)

def parse_bca(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    period_val = ""
    period_match = re.search(r"PERIODE\s*[:]\s*(.+)", text, re.IGNORECASE)
    if period_match:
        period_val = period_match.group(1).strip()
    
    # Try to find Year from Period (e.g. "OKTOBER 2025")
    current_year = str(datetime.now().year)
    year_match = re.search(r"\d{4}", period_val)
    if year_match:
        current_year = year_match.group(0)

    initial_balance = 0.0
    closing_balance = 0.0
    incoming_trans = 0.0
    outgoing_trans = 0.0 # Summaries might not be on first page, but we capture what we can
    
    transactions = []
    current_trans = None
    
    # Regex to catch the main transaction line:
    # 07/10   TRSF ...   135,700.00 DB   909,571.93
    # Note: Description can contain anything. Amount is roughly at the end.
    # We look for the Date at start, and Amount structure near end.
    
    for i, line in enumerate(lines):
        # Skip page headers/footers/summaries
        if "REKENING TAHAPAN" in line or "NO. REKENING" in line or "HALAMAN" in line: continue
        if "CATATAN" in line or "Bersambung" in line: continue
        if "TANGGAL" in line and "KETERANGAN" in line: continue # Table header
        
        # New Exclusions for Metadata/Footers/Summaries
        if re.search(r"KCU\s+[A-Z]+", line): 
             current_trans = None; continue
        if "PERIODE" in line or "MATA UANG" in line: 
             current_trans = None; continue
             
        # Handle Summaries (Extraction + Skip)
        if "SALDO AWAL" in line.upper():
             bal_match = re.search(r"([\d,]+\.\d{2})$", line)
             if bal_match:
                 initial_balance = clean_amount(bal_match.group(1))
             current_trans = None
             continue # Skip parsing as transaction
             
        if "SALDO AKHIR" in line.upper():
             # Can extract closing balance if needed, though usually calculated
             current_trans = None
             continue
             
        if re.search(r"MUTASI\s+(CR|DB)", line): 
             current_trans = None; continue
             
        if re.search(r"(APABILA|BERHAK|SEGALA DATA|UANG ANDA)", line, re.IGNORECASE): 
             current_trans = None; continue

        # 1. Check for Date at start: DD/MM
        date_match = re.search(r"^(\d{2})/(\d{2})", line)
        if date_match:
            # New Entry
            day = date_match.group(1)
            month = date_match.group(2)
            
            # Check for SALDO AWAL (Initial Balance)
            if "SALDO AWAL" in line.upper():
                 # Extract balance at end
                 # "01/10 SALDO AWAL ... 1,045,271.93"
                 bal_match = re.search(r"([\d,]+\.\d{2})$", line)
                 if bal_match:
                     initial_balance = clean_amount(bal_match.group(1))
                 current_trans = None
                 continue
            
            # Start new transaction
            # Extract Amount and Balance
            # Pattern: (Amount) (DB)? (Balance)?
            # Regex: Find all numbers resembling currency
            nums = re.findall(r"([\d,]+\.\d{2})", line)
            
            amount = 0.0
            balance = 0.0
            amount_type = "credit" # default, unless DB found
            
            has_db = "DB" in line.upper()
            if has_db: amount_type = "debit"
            
            # Heuristic: 
            # If 2 numbers: first is Amount, second is Balance
            # If 1 number:
            #    If "DB" is after it, likely Amount. Balance might be missing?
            #    Or check position/context.
            
            if len(nums) >= 2:
                amount = clean_amount(nums[0])
                balance = clean_amount(nums[-1])
            elif len(nums) == 1:
                amount = clean_amount(nums[0])
                # If only 1 number, implies no balance shown (maybe blocked by text? or just omitted)
                # But usually balance is rightmost.
                # However, Amount is the critical one.
                # Is it amount or balance?
                # Usually Transaction lines HAVE an amount. 
                # If "DB" is present, the number near it is Amount.
            
            # Extract Description: Everything between Date and Amount
            # Logic: Remove Date. Remove Amount/Balance/DB from end. 
            # Remaining is desc.
            desc_part = line[5:] # Skip DD/MM
            desc_part = re.sub(r"([\d,]+\.\d{2}).*", "", desc_part).strip() # Remove amount onwards
            # Note: this is aggressive if Description has numbers.
            # Better: `line.split(amount_str)[0]`
            
            # Let's use the found amount string to split
            if len(nums) > 0:
                amount_str = nums[0]
                parts = line.split(amount_str)
                desc_text = parts[0][5:].strip() # After date, before amount
            else:
                desc_text = line[5:].strip() # Fallback
            
            current_trans = {
                "day": day,
                "month": month,
                "year": current_year,
                "description": desc_text,
                "amount": amount,
                "type": amount_type,
                "balance": balance
            }
            transactions.append(current_trans)
            
        else:
            # Continuation line (Description)
            if current_trans is not None:
                # Append to description
                # Skip if it looks like noise
                if re.match(r"^\d{4}/", line): # Reference numbers often look like this
                     current_trans["description"] += " " + line
                elif re.match(r"^[A-Z0-9\s-]+$", line) or re.search(r"[a-z]", line): # Alphanumeric text
                     current_trans["description"] += " " + line
                else:
                     # Maybe metadata
                     current_trans["description"] += " " + line

    # Finalize transactions list
    final_transactions = []
    for t in transactions:
        final_transactions.append({
            "transaction_date": f"{t['year']}-{t['month']}-{t['day']}",
            "transaction_description": re.sub(r"\s+", " ", t['description']).strip(),
            "transaction_amount": t['amount'],
            "amount_type": t['type'],
            "transaction_bank": "BCA",
            "transaction_balance": t['balance']
        })
        
        # Aggregate logic if Summaries are missing?
        # User manual sample doesn't show "Total Mutasi", so we might need to sum them up?
        # Or leave as 0.0 if not explicit.
        # But User asked specifically to "Refactor ... please dont impact mandiri".
        # Existing parse_bca had mutasi extraction.
        # If we can't find them, we can calc them?
        if t['type'] == 'credit':
             incoming_trans += t['amount']
        else:
             outgoing_trans += t['amount']

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": final_transactions[-1]['transaction_balance'] if final_transactions else initial_balance,
        "incoming_transactions": incoming_trans,
        "outgoing_transactions": outgoing_trans,
        "transactions": final_transactions
    }

def parse_mandiri(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    period_val = ""
    for idx, line in enumerate(lines):
         # Try to find period in header lines
         if re.search(r"Periode|Period", line, re.IGNORECASE):
            # Check current line first
            p_match = re.search(r"(\d{2}\s[A-Za-z]{3}\s\d{4}\s*-\s*\d{2}\s[A-Za-z]{3}\s\d{4})", line)
            if p_match:
                period_val = p_match.group(1)
                break
                
            # Check Next lines
            for off in range(1, 20):
                if idx+off >= len(lines): break
                cand = lines[idx+off]
                p_match = re.search(r"(\d{2}\s[A-Za-z]{3}\s\d{4}\s*-\s*\d{2}\s[A-Za-z]{3}\s\d{4})", cand)
                if p_match:
                    period_val = p_match.group(1)
                    break
            if period_val: break

    def find_mandiri_val(label_pattern):
        for idx, line in enumerate(lines):
            if re.search(label_pattern, line, re.IGNORECASE):
               # 1. Check same line if colon exists
               if ":" in line:
                   parts = line.split(":")
                   for p in reversed(parts):
                       try:
                           # Must allow dots/commas
                           if re.search(r"[\d.,]+", p):
                               val = clean_amount(p)
                               return val
                       except ValueError:
                           continue
               
               # 2. Look forward up to 15 lines
               for off in range(1, 16):
                    if idx+off >= len(lines): break
                    cand = lines[idx+off]
                    
                    # Skip metadata lines
                    if re.search(r"Nomor Rekening|Account Number|Cabang|Branch|Mata Uang|Currency", cand, re.IGNORECASE): continue
                    
                    # Skip date ranges
                    if re.search(r"[A-Za-z]{3}", cand) and re.search(r"\d{4}", cand): continue
                    if "-" in cand and not cand.strip().startswith("-") and not re.search(r"\d", cand): continue 
                    
                    if not re.search(r"[.,]", cand) and cand.strip() != "0": continue
                    
                    if re.search(r"[\d]+", cand):
                         try:
                             return clean_amount(cand)
                         except ValueError:
                             continue
        return 0.0

    # Summary fields - FORCE POSITIVE for incoming/outgoing as requested
    initial_balance = find_mandiri_val(r"Saldo\s*Awal")
    closing_balance = find_mandiri_val(r"Saldo\s*Akhir")
    incoming_trans = abs(find_mandiri_val(r"Dana\s*Masuk"))
    outgoing_trans = abs(find_mandiri_val(r"Dana\s*Keluar"))

    transactions = []
    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Mei": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }
    
    for i, line in enumerate(lines):
        # Exclude Header/Summary lines
        if re.search(r"Saldo\s*Awal|Saldo\s*Akhir|Dana\s*Masuk|Dana\s*Keluar|Initial\s*Balance|Closing\s*Balance|Incoming\s*Transactions|Outgoing\s*Transactions", line, re.IGNORECASE):
            continue

        amount_match = re.search(r"([+-]\s*[\d.]+,[\d]{2})", line)
        if amount_match:
            raw_val = amount_match.group(1).replace(" ", "")
            
            if "+" in raw_val or "CR" in line:
                current_amount_type = "credit" 
            else:
                current_amount_type = "debit" 
                
            current_amount = clean_amount(raw_val) 
            if "-" in raw_val: current_amount = abs(current_amount)
            
            transaction_balance = 0.0
            
            # Balance extraction (Forward/Same line)
            nums = re.findall(r"([\d.]+,[\d]{2})", line)
            found_bal = False
            for fwd in range(1, 5):
                if i + fwd >= len(lines): break
                next_l = lines[i+fwd]
                if re.match(r"^[\d.]+,[\d]{2}$", next_l):
                     transaction_balance = clean_amount(next_l)
                     found_bal = True
                     break
            
            if not found_bal and len(nums) > 1:
                candidate = nums[-1]
                if abs(clean_amount(candidate) - current_amount) > 0.01:
                     transaction_balance = clean_amount(candidate)

            # Capture text from the CURRENT line (Amount line)
            curr_line_clean = line
            curr_line_clean = re.sub(r"[+-]?\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2}", "", curr_line_clean)
            curr_line_clean = re.sub(r"^\s*\d+\s+", " ", curr_line_clean)
            curr_line_clean = re.sub(r"\d{2}:\d{2}:\d{2}\s*WIB", "", curr_line_clean)
            curr_line_clean = curr_line_clean.strip()

            # Look Backward for Description
            desc_lines = []
            tx_date = ""
            
            for back in range(1, 20):
                if i - back < 0: break
                p_line = lines[i - back]
                
                # STOP if we hit the previous transaction's Amount line (contains digits, commas, dots)
                # But be careful not to trigger on the CURRENT line (since we start back=1)
                # Previous amount line example: "1 ... -50.000,00 ... 166.000,00"
                if re.search(r"[\d.]+,[\d]{2}", p_line) and re.search(r"[+-]", p_line): 
                    break 

                # Skip numeric lines that are just numbers (like independent balances)
                if re.match(r"^[\d.]+,[\d]{2}$", p_line): continue 
                if re.match(r"^\d+$", p_line): continue # Index numbers ("1", "2")
                
                # Keywords to ignore
                if re.search(r"Saldo|Balance|Nominal|Amount|Keterangan|Remarks|Date|Tanggal", p_line, re.IGNORECASE): continue
                if "No" == p_line: continue
                
                # Date check
                d_match = re.search(r"(\d{2})\s(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s(\d{4})", p_line, re.IGNORECASE)
                if d_match:
                    if tx_date: # We already found a date, this is a SECOND date (prev transaction?)
                        break

                    day = d_match.group(1)
                    month_str = d_match.group(2).title()
                    year = d_match.group(3)
                    month = month_map.get(month_str, "01")
                    tx_date = f"{year}-{month}-{day}"
                    
                    clean_p = re.sub(r"\d{2}\s[A-Za-z]{3}\s\d{4}", "", p_line).strip()
                    if clean_p and not re.search(r"\d{2}:\d{2}:\d{2}", clean_p):
                        desc_lines.insert(0, clean_p)
                    
                    # Continue scanning to capture lines above the date
                    continue
                
                if re.search(r"\d{2}:\d{2}:\d{2}", p_line): continue

                desc_lines.insert(0, p_line)
            
            if tx_date:
                 full_desc = " ".join(desc_lines).strip()
                 full_desc = re.sub(r"^\d+\s+", "", full_desc) # leading index
                 
                 if curr_line_clean:
                     full_desc += " " + curr_line_clean
                 
                 transactions.append({
                    "transaction_date": tx_date,
                    "transaction_description": full_desc,
                    "transaction_amount": current_amount,
                    "amount_type": current_amount_type,
                    "transaction_bank": "MANDIRI",
                    "transaction_balance": transaction_balance
                 })

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_trans,
        "outgoing_transactions": outgoing_trans,
        "transactions": transactions
    }
def parse_bni(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    period_val = ""
    initial_balance = 0.0
    closing_balance = 0.0
    incoming_trans = 0.0
    outgoing_trans = 0.0
    transactions = []
    
    # helper for BNI currency: "118,090" -> 118090.0
    # "38,595" -> 38595.0
    # It seems BNI PDF uses comma as thousands separator for IDR? 
    # Or maybe it's just whole numbers?
    # Let's handle both: if result < 1.0 (meaning it parsed 118,090 as 118.09), multiply?
    # No, safer to remove commas if they appear to be thousands.
    # But clean_amount assumes Indonesian locale (dot=thousands, comma=decimal).
    # If BNI swaps this, we need custom logic.
    # Given "+10,000" (10k), standard 'clean_amount' ("10.000") w/ replacement would fail if passed "10,000" (it would think 10.0).
    # Let's inspect the `clean_amount` first.
    
    def parse_bni_amount(s):
        # Remove signs
        clean_s = s.replace("+", "").replace("-", "")
        # If it matches "123,456", it's likely 123456.
        # If "123.456", it might be 123456 (if dot is thousands).
        # In the sample: "118,090". 
        # Most likely: Comma is thousands separator.
        clean_s = clean_s.replace(",", "")
        return float(clean_s)

    current_year = str(datetime.now().year)

    for i, line in enumerate(lines):
        # Header Metadata
        if "Periode:" in line:
            # "Periode: 1 - 30 November 2025"
            period_val = line.split("Periode:")[-1].strip()
            # Try extract year
            y_match = re.search(r"\d{4}", period_val)
            if y_match: current_year = y_match.group(0)

        # Summaries
        # "Saldo Awal 118,090" or "Saldo Awal" then next line? 
        # In sample, it looks like a table row: "Saldo Awal Total Pemasukan ..."
        # followd by values line: "118,090 +38,595 ..."
        # We'll use lookahead/scan logic.
        
        if "Saldo Awal" in line and "Total Pemasukan" in line:
            # The NEXT line likely has the values
            if i + 1 < len(lines):
                val_line = lines[i+1]
                parts = val_line.split()
                # Expected: [SaldoAwal, In, Out, SaldoAkhir]
                # "118,090 +38,595 -5,000 151,685"
                # Need to be robust. Regex find all signed/unsigned numbers.
                nums = re.findall(r"[+-]?[\d,]+", val_line)
                if len(nums) >= 4:
                    initial_balance = parse_bni_amount(nums[0])
                    incoming_trans = abs(parse_bni_amount(nums[1]))
                    outgoing_trans = abs(parse_bni_amount(nums[2]))
                    closing_balance = parse_bni_amount(nums[-1])
        
        # Also catch explicit lines if they appear separately (just in case)
        if line.startswith("Saldo Awal") and not "Total" in line:
             # Look for number at end
             m = re.search(r"([\d,]+)$", line)
             if m: initial_balance = parse_bni_amount(m.group(1))

    # Transactions
    # Pattern:
    # Date line: "10 Nov 2025 Transfer"
    # Detail line: "08:37:35 WIB MANDIRI ..."
    # Amount line: "+10,000 128,090" ??
    # Debug output showed: "10 Nov 2025 Transfer"
    # followed by "+10,000 128,090" likely on same line or next?
    # Real layout is tricky.
    # Let's iterate and look for Date.
    
    curr_trans = None
    
    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Mei": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Des": "12", "Dec": "12"
    }

    for i, line in enumerate(lines):
        # Skip headers/footers
        if "Laporan Mutasi" in line or "Periode:" in line or "Rincian Transaksi" in line: continue
        if "Saldo Awal" in line: continue 
        if "Total Pemasukan" in line or "Total Pengeluaran" in line: continue
        
        if "Saldo Akhir" in line: 
             curr_trans = None; continue
        if "Informasi Lainnya" in line: 
             curr_trans = None; continue
        if "Apabila terdapat" in line: 
             curr_trans = None; continue
        if "Dokumen ini" in line: 
             curr_trans = None; continue
        if "PT Bank Negara Indonesia" in line or "berizin dan diawasi" in line: 
             curr_trans = None; continue
        if "Lembaga Penjamin Simpanan" in line or "1 dari" in line:
             curr_trans = None; continue
        
        # Date Match: "10 Nov 2025" or "10-Nov-2025"
        date_match = re.match(r"^(\d{1,2})\s([A-Za-z]{3})\s(\d{4})", line)
        if date_match:
            day = date_match.group(1).zfill(2)
            month_str = date_match.group(2)
            year = date_match.group(3)
            month = month_map.get(month_str, "01")
            formatted_date = f"{year}-{month}-{day}"
            
            # Start New Transaction
            # Check for immediate Amount on this line (if sort=True merged them)
            # Regex for Amount: [+-][\d,]+
            # Regex for Balance: [\d,]+ (at end)
            
            amt_match = re.search(r"([+-])([\d,]+)", line)
            
            amount = 0.0
            type_str = "credit"
            balance = 0.0
            desc = line.replace(date_match.group(0), "").strip()
            
            if amt_match:
                sign = amt_match.group(1)
                val_s = amt_match.group(2)
                amount = parse_bni_amount(val_s)
                type_str = "credit" if sign == "+" else "debit"
                
                # Assume Balance is after amount
                # Find number at end of line
                bal_match = re.search(r"([\d,]+)$", line)
                if bal_match:
                    balance = parse_bni_amount(bal_match.group(1))
                    
                # Clean desc (remove amount/balance)
                desc = desc.replace(amt_match.group(0), "")
                if bal_match: desc = desc.replace(bal_match.group(0), "")
                desc = desc.strip()
            
            curr_trans = {
                "date": formatted_date,
                "desc": desc,
                "amount": amount,
                "type": type_str,
                "balance": balance
            }
            transactions.append(curr_trans)
            continue
            
        # If not date line, check if it's metadata attached to current transaction
        # Timestamp: "08:37:35 WIB"
        if curr_trans:
            # If line has amount and we didn't find it yet?
            if curr_trans['amount'] == 0.0:
                 amt_match = re.search(r"([+-])([\d,]+)", line)
                 if amt_match:
                    sign = amt_match.group(1)
                    val_s = amt_match.group(2)
                    curr_trans['amount'] = parse_bni_amount(val_s)
                    curr_trans['type'] = "credit" if sign == "+" else "debit"
                     # Balance
                    bal_match = re.search(r"([\d,]+)$", line)
                    if bal_match:
                        curr_trans['balance'] = parse_bni_amount(bal_match.group(1))
                    continue # Extracted amount, rest acts as desc?
            
            if re.search(r"\d{2}:\d{2}:\d{2}", line):
               curr_trans['desc'] += " " + line
            elif "Transfer" in line or "MANDIRI" in line or "BNI" in line:
               curr_trans['desc'] += " " + line
            elif line.strip() and not "Saldo" in line: # Generic text
               curr_trans['desc'] += " " + line
               
    # Final cleanup
    final_transactions = []
    for t in transactions:
        final_transactions.append({
            "transaction_date": t['date'],
            "transaction_description": re.sub(r"\s+", " ", t['desc']).strip(),
            "transaction_amount": t['amount'],
            "amount_type": t['type'],
            "transaction_bank": "BNI",
            "transaction_balance": t['balance']
        })

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_trans,
        "outgoing_transactions": outgoing_trans,
        "transactions": final_transactions
    }
def parse_blu(text: str) -> dict:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    period_val = ""
    initial_balance = 0.0
    closing_balance = 0.0
    incoming_trans = 0.0
    outgoing_trans = 0.0
    transactions = []
    
    # 1. Period & Summaries
    # Text flow: "November 2025 ... Rp 136.953.701,81 Rp 213.144,38" (Income | Initial)
    # Text flow: "IDR (Rp) ... Rp 135.841.094,42 Rp 1.325.751,77" (Expense | Closing)
    
    # Find period - usually strictly "Month YYYY" under Header
    # Regex for "November 2025" or "Nov 2025"
    p_match = re.search(r"Periode / Period\s+([A-Za-z]+\s\d{4})", text.replace("\n", " "))
    if not p_match:
         # Try matching just the date line if header missing
         p_match = re.search(r"\n\s*([A-Za-z]+\s\d{4})\s+Rp", text)
         
    if p_match:
        period_val = p_match.group(1).strip()
    else:
        # Fallback: Find lines looking like "November 2025" between Name and Income
        # Just grab the first "Month YYYY" found?
        pass

    # Extract Summaries by Label Context is hard because values are far.
    # But values are distinct: "Rp ..."
    # Let's find "Saldo Awal" line index, look around.
    # Actually, `sort=True` result presented:
    # Name Per INC INIT
    # Acc Curr EXP END
    
    # Let's clean all "Rp" values first
    rp_values = re.findall(r"Rp\s*([\d.]+,[\d]{2})", text)
    if len(rp_values) >= 4:
        # Heuristic based on position in list:
        # Order in text: Income, Initial, Expense, Ending?
        # Output: "Rp 136... Rp 213..." -> Income, Initial
        # Output: "Rp 135... Rp 1.325..." -> Expense, Ending
        # It seems consistent.
        
        # We can try to be more specific by finding the line containin "Saldo Awal"
        pass
        
    # Better approach: Iterate lines for key phrases
    for i, line in enumerate(lines):
        if "Periode / Period" in line:
             # Next line might have it? Or same line?
             # Sample:
             # "Name Periode / Period Total Pemasukan / Total Income Saldo Awal / Initial Balance"
             # "Made Rezananda Putra November 2025 Rp 136.953.701,81 Rp 213.144,38"
             if i+1 < len(lines):
                  next_l = lines[i+1]
                  # Regex to pull parts: Name | Date | Rp... | Rp...
                  # It's tricky.
                  # Let's grab just the Rp values from that line.
                  vals = re.findall(r"Rp\s*([\d.]+,[\d]{2})", next_l)
                  if len(vals) >= 2:
                      incoming_trans = clean_amount(vals[0])
                      initial_balance = clean_amount(vals[1])
                  # Grab period from that line
                  # remove Rps, trim digits
                  temp = re.sub(r"Rp\s*[\d.]+,[\d]{2}", "", next_l)
                  # temp = "Made Rezananda Putra November 2025"
                  # Assuming name doesn't have digits
                  d_match = re.search(r"([A-Za-z]+\s\d{4})$", temp.strip())
                  if d_match: period_val = d_match.group(1)

        if "Saldo Akhir / Ending Balance" in line:
             if i+1 < len(lines):
                  next_l = lines[i+1]
                  vals = re.findall(r"Rp\s*([\d.]+,[\d]{2})", next_l)
                  if len(vals) >= 2:
                      outgoing_trans = clean_amount(vals[0]) # Expense
                      closing_balance = clean_amount(vals[1]) # Ending

    # Transactions
    curr_trans = None
    
    for i, line in enumerate(lines):
        # Skip headers
        if "bluAccount" in line or "Halaman" in line: continue
        if "Periode / Period" in line or "Mata Uang" in line: continue
        if "Detail Transaksi" in line: continue
        if "Total Pemasukan" in line or "Saldo Awal" in line: continue
        if "Total Pengeluaran" in line or "Saldo Akhir" in line: continue
        
        # Skip rows we already processed for summaries (containing Rp val AND summary keywords nearby?)
        # Just check if line starts with Date.
        
        # Date Match: "01 Nov 2025"
        date_match = re.match(r"^(\d{2})\s([A-Za-z]{3})\s(\d{4})", line)
        
        if date_match:
            # Start New
            if curr_trans: transactions.append(curr_trans)
            
            day = date_match.group(1)
            month = date_match.group(2)
            year = date_match.group(3)
            
            month_map = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06", 
                         "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
            m_num = month_map.get(month, "01")
            
            # Description is usually on this line (after Date) OR next lines
            desc_text = line[11:].strip() 
            
            curr_trans = {
                "date": f"{year}-{m_num}-{day}",
                "desc": desc_text,
                "amount": 0.0,
                "type": "debit", # default
                "balance": 0.0
            }
            continue
            
        # If inside transaction, look for Amount/Balance line
        if curr_trans:
            # Check for Amount pattern: "1.000.000,00" or "- 25.000,00"
            # Followed by Balance+Time?
            # Regex: `([-]?\s*[\d.]+,[\d]{2})\s+([\d.]+,[\d]{2})(\d{2}:\d{2})?`
            
            # Clean spaces in negative sign: "- 25" -> "-25"
            line_clean = line.replace("- ", "-")
            
            # Find all numbers
            # Check if line ends with time "HH:MM"?
            time_match = re.search(r"(\d{2}:\d{2})$", line_clean)
            has_time = False
            if time_match: has_time = True
            
            # Find potential amount/balance values
            # Group 1: Amount (with optional sign)
            # Group 2: Balance (merged with Time)
            # Sample: "-25.000,00 188.144,3806:59"
            
            # Regex to find Amount at start or middle
            # Look for 2 currency numbers
            nums = re.findall(r"([-]?[\d.]+,[\d]{2})", line_clean)
            
            if len(nums) >= 2:
                # Likely Amount and Balance
                amt_str = nums[0]
                bal_str = nums[1] # This might be "188.144,38" correctly parsed if comma stops greedy
                
                # Check for merged time in the raw line to be safe
                # "188.144,3806:59" -> re.findall would pluck "188.144,38" and "06"??
                # Actually, `[\d]{2}` matches "38". "06" starts next.
                # So regex might be clean.
                
                # Let's verify if `bal_str` is clean.
                # If valid currency -> parse.
                # If negative, Type=Debit.
                
                curr_trans['amount'] = abs(clean_amount(amt_str))
                curr_trans['type'] = "debit" if "-" in amt_str else "credit"
                
                # Handling merged time:
                # If the line text actually has merged digits, re.findall might have cut it cleanly ONLY IF there was a separator.
                # Expected: "188.144,3806:59"
                # Regex `[\d]+,[\d]{2}` will match `188.144,38`. The `06:59` remains.
                # So `nums` should be clean.
                
                curr_trans['balance'] = clean_amount(bal_str)
                
                # If logic matches, assume this line consumed?
                # Sometimes desc continues?
                # Usually this line is purely numbers in BLU layout?
                # Sample: "- 25.000,00 188.144,3806:59" -> Yes just numbers/time.
                
                # Add time to desc? Not required but nice.
                if has_time: 
                     curr_trans['desc'] += " " + time_match.group(1)
                     
                # Finalize this trans in loop? No, wait for next date.
                # But prevent re-matching numbers if multiple lines have numbers (unlikely).
                
                continue 

            # If not numbers, append to description
            # Exclude footer text
            if "BCA Digital" in line or "haloblu" in line: 
                curr_trans = None # End of page
                continue
                
            if curr_trans and line.strip():
                curr_trans['desc'] += " " + line.strip()

    if curr_trans: transactions.append(curr_trans)

    # Convert to final list
    final_transactions = []
    for t in transactions:
        final_transactions.append({
            "transaction_date": t['date'],
            "transaction_description": re.sub(r"\s+", " ", t['desc']).strip(),
            "transaction_amount": t['amount'],
            "amount_type": t['type'],
            "transaction_bank": "BLU",
            "transaction_balance": t['balance']
        })

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_trans,
        "outgoing_transactions": outgoing_trans,
        "transactions": final_transactions
    }
