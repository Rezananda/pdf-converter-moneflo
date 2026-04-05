import re
import traceback
from datetime import datetime

# ─────────────────────────────────────────────────────────────────────────────
# SHARED CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

MONTH_MAP = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "mei": "05",
    "jun": "06", "jul": "07", "aug": "08", "agu": "08", "sep": "09",
    "oct": "10", "okt": "10", "nov": "11", "dec": "12", "des": "12",
    "januari": "01", "februari": "02", "maret": "03", "april": "04",
    "juni": "06", "juli": "07", "agustus": "08", "september": "09",
    "oktober": "10", "november": "11", "desember": "12",
    "january": "01", "march": "03", "june": "06", "july": "07",
    "august": "08", "september": "09", "october": "10", "november": "11",
    "december": "12"
}

# ─────────────────────────────────────────────────────────────────────────────
# VALIDATION
# ─────────────────────────────────────────────────────────────────────────────

def validate_bank_statement(text: str) -> bool:
    """Smart validation: checks if this PDF text looks like a bank statement."""
    bank_keywords = [
        r"saldo", r"mutasi", r"rekening", r"keterangan", r"nominal",
        r"debit", r"credit", r"transaction", r"pemasukan", r"pengeluaran",
        r"amount", r"balance", r"statement", r"history", r"transfer",
        r"tabungan", r"account", r"savings", r"kantong", r"pockets"
    ]
    matches = sum(1 for kw in bank_keywords if re.search(kw, text, re.IGNORECASE))
    date_pattern = r"\d{1,2}[/\-\s]([A-Za-z]{3,}|\d{1,2})[/\-\s]?\d{0,4}"
    has_dates = len(re.findall(date_pattern, text)) > 2
    currency_pattern = r"\d{1,3}(?:[.,]\d{3})+[.,]\d{2}"
    has_currency = len(re.findall(currency_pattern, text)) > 3
    return matches >= 3 or (matches >= 2 and has_dates) or (has_dates and has_currency)


# ─────────────────────────────────────────────────────────────────────────────
# BANK SIGNATURE DETECTION
# ─────────────────────────────────────────────────────────────────────────────

def detect_bank(text: str, metadata: dict) -> str:
    """Auto-detect the bank from metadata and text content."""
    creator = metadata.get("creator", "") if metadata else ""
    
    # Metadata signatures (most reliable)
    if "Bank Mandiri" in creator:
        return "MANDIRI"
    if "E-statement Batch Generator" in creator or "BCA" in creator.upper():
        return "BCA"
    if "BNI" in creator.upper() or "Bank Negara Indonesia" in creator:
        return "BNI"
    
    # Content signatures (ordered by specificity — Jago MUST come before BLU)
    # because Jago statements often mention "BCA Digital" as a transfer source.
    if "Pockets Transactions History" in text or "Kantong Utama" in text:
        return "JAGO"
    if "bluAccount" in text or "bluSaving" in text:
        return "BLU"
    if "PT BANK SEABANK INDONESIA" in text.upper() or "www.seabank.co.id" in text:
        return "SEABANK"
    if "Tabungan NOW" in text or "Mandiri Call" in text or ("Bank Mandiri" in text and "Dana Masuk" in text):
        return "MANDIRI"
    if "MUTASI REKENING" in text and "BCA" in text:
        return "BCA"
    if ("TAPLUS" in text or "BNI" in text) and "Negara Indonesia" in text:
        return "BNI"
    if "e-Statement" in text and ("Tabungan" in text or "Semangat" in text):
        return "MANDIRI"
    
    return "UNKNOWN"


# ─────────────────────────────────────────────────────────────────────────────
# BANK ROUTER
# ─────────────────────────────────────────────────────────────────────────────

def parse_bank_statement(text: str, metadata: dict, filename: str = "") -> dict:
    bank = detect_bank(text, metadata)
    print(f"DEBUG: Detected bank = '{bank}' for file '{filename}'")
    
    if bank == "MANDIRI":
        return parse_mandiri(text)
    if bank == "BCA":
        return parse_bca(text)
    elif bank == "BNI":
        return parse_bni(text)
    elif bank == "BLU":
        return parse_blu(text)
    elif bank == "JAGO":
        return parse_jago(text)
    elif bank == "SEABANK":
        return parse_seabank(text)
    else:
        print("DEBUG: Unknown bank. Using Smart Adaptive Parser.")
        return parse_smart(text, metadata)


# ─────────────────────────────────────────────────────────────────────────────
# JAGO PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_jago(text: str) -> dict:
    """
    Parser for Bank Jago statements.
    Layout: DD MMM YYYY  Source/Dest  Description  Note  +/-Amount  Balance
    HH.MM is on a second sub-line.
    Dates are grouped under month headers like 'Januari 2026'.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    period_val = ""
    initial_balance = 0.0
    closing_balance = 0.0
    incoming_total = 0.0
    outgoing_total = 0.0
    transactions = []
    current_year = str(datetime.now().year)

    # Detect period from header
    # "Menampilkan transaksi IDR dari 23 Jan 2026 - 05 Apr 2026"
    period_match = re.search(
        r"dari\s+(\d{1,2}\s+\w+\s+\d{4})\s*[-–]\s*(\d{1,2}\s+\w+\s+\d{4})",
        text, re.IGNORECASE
    )
    if period_match:
        period_val = f"{period_match.group(1)} - {period_match.group(2)}"
        yr = re.search(r"(\d{4})", period_match.group(2))
        if yr:
            current_year = yr.group(1)

    # Detect closing balance from "Saldo terbaru ... IDR 408.773"
    closing_match = re.search(r"IDR\s+([\d.,]+)", text)
    if closing_match:
        closing_balance = clean_amount(closing_match.group(1))

    # Current month context (tracks 'Januari 2026' headers)
    current_month = "01"
    current_year_ctx = current_year

    # Transaction pattern: "DD MMM YYYY  Source  Description    Note    +/-Amount  Balance"
    # Amount uses Indonesian format: 500.000,00 or 100.000.000
    # Some amounts have no decimal: 82 or -500.000

    tx_date_pattern = re.compile(
        r"^(\d{1,2})\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des|"
        r"Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|"
        r"January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})",
        re.IGNORECASE
    )
    month_header_pattern = re.compile(
        r"^(Januari|Februari|Maret|April|Mei|Juni|Juli|Agustus|September|Oktober|November|Desember|"
        r"January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})$",
        re.IGNORECASE
    )
    currency_pattern = re.compile(r"([+-]?\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|\d+)")

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check for month header (e.g., "Januari 2026")
        mhdr = month_header_pattern.match(line)
        if mhdr:
            current_month = MONTH_MAP.get(mhdr.group(1).lower(), "01")
            current_year_ctx = mhdr.group(2)
            i += 1
            continue

        # Check for transaction date row
        tx_match = tx_date_pattern.match(line)
        if tx_match:
            day = tx_match.group(1).zfill(2)
            month_str = tx_match.group(2).lower()[:3]
            year = tx_match.group(3)
            month = MONTH_MAP.get(month_str, current_month)
            tx_date = f"{year}-{month}-{day}"

            # Everything after the date on this line is potentially source/desc/amount/balance
            remaining = line[tx_match.end():].strip()

            # Extract all currency-looking numbers from this line
            # Jago amounts: "+500.000,00" or "-100.000.000" or "+82"
            amounts_in_line = re.findall(
                r"([+-]\s*\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?|[+-]\d+)",
                remaining
            )
            balances_in_line = re.findall(
                r"(?<![+-])\b(\d{1,3}(?:\.\d{3})*(?:,\d{2})?)\b",
                remaining
            )

            # Build description from non-numeric parts
            desc_part = re.sub(
                r"[+-]?\s*\d{1,3}(?:\.\d{3})*(?:,\d{1,2})?", "", remaining
            ).strip()
            desc_part = re.sub(r"\s+", " ", desc_part).strip()

            # Get next line (often the time + extra detail)
            if i + 1 < len(lines) and re.match(r"^\d{2}\.\d{2}", lines[i+1]):
                time_line = lines[i + 1]
                extra = re.sub(r"^\d{2}\.\d{2}\s*", "", time_line).strip()
                if extra:
                    desc_part = (desc_part + " " + extra).strip()
                i += 1  # consume time line

            # Determine amount and type
            amount = 0.0
            amount_type = "credit"
            balance = 0.0

            if amounts_in_line:
                raw_amt = amounts_in_line[0].replace(" ", "")
                amount = abs(clean_amount(raw_amt))
                amount_type = "debit" if "-" in raw_amt else "credit"

            if balances_in_line:
                balance = clean_amount(balances_in_line[-1])

            # Accumulate totals
            if amount_type == "credit":
                incoming_total += amount
            else:
                outgoing_total += amount

            transactions.append({
                "transaction_date": tx_date,
                "transaction_description": desc_part,
                "transaction_amount": amount,
                "amount_type": amount_type,
                "transaction_bank": "JAGO",
                "transaction_balance": balance
            })

        i += 1

    if transactions:
        closing_balance = transactions[-1]["transaction_balance"] or closing_balance

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_total,
        "outgoing_transactions": outgoing_total,
        "transactions": transactions
    }


# ─────────────────────────────────────────────────────────────────────────────
# SEABANK PARSER
# ─────────────────────────────────────────────────────────────────────────────

def parse_seabank(text: str) -> dict:
    """
    Parser for SeaBank statements.
    After sort=True, transaction rows look like:
      "02 JUN                 124.192"   (date + amount on same line)
      "Shopee"                           (description on next line)
      "Payment"                          (description type on next line)
    Or for incoming:
      "02 JUN                             184.277"
      "Ni Luh Gede Sri Fajaryani"
      "Transfer"
    """
    # Use raw lines (strip only leading/trailing whitespace, keep internal spacing)
    raw_lines = [l.rstrip() for l in text.split("\n")]
    lines = [l.strip() for l in raw_lines if l.strip()]

    period_val = ""
    initial_balance = 0.0
    closing_balance = 0.0
    incoming_total = 0.0
    outgoing_total = 0.0
    transactions = []

    # Detect year
    year_match = re.search(r"\b(20\d{2})\b", text)
    current_year = year_match.group(1) if year_match else str(datetime.now().year)

    # Detect period
    period_match = re.search(
        r"(\d{1,2}\s+\w{3,}\s+\d{4})\s+(?:to|-)\s+(\d{1,2}\s+\w{3,}\s+\d{4})",
        text, re.IGNORECASE
    )
    if period_match:
        period_val = f"{period_match.group(1)} - {period_match.group(2)}"
        current_year = re.search(r"\d{4}", period_match.group(1)).group(0)

    # Summary from raw text: "SAVINGS  20.152.067  13.076.837  111.836.081  118.911.311"
    summary_match = re.search(
        r"SAVINGS\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)\s+([\d.,]+)", text
    )
    if summary_match:
        initial_balance = clean_amount(summary_match.group(1))
        outgoing_total = clean_amount(summary_match.group(2))
        incoming_total = clean_amount(summary_match.group(3))
        closing_balance = clean_amount(summary_match.group(4))

    # Transaction row pattern (on the same stripped line):
    # "DD MMM  [whitespace]  AMOUNT"
    # Seabank uses two columns: OUTGOING at ~col 50, INCOMING at ~col 70+
    # We detect amounts on the date line by looking at the raw (unstripped) line
    
    # We'll process raw_lines to preserve column positions for amount classification
    date_line_re = re.compile(
        r"^\s{0,8}(\d{1,2})\s+(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)",
        re.IGNORECASE
    )
    month_line_re = re.compile(
        r"^\s{0,8}(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\s*$",
        re.IGNORECASE
    )
    
    skip_keywords = {
        "DATE", "TRANSACTION", "OUTGOING (IDR)", "INCOMING (IDR)",
        "SAVINGS - TRANSACTION DETAILS", "ACCOUNT SUMMARY",
        "STARTING BALANCE (IDR)", "TOTAL OUTGOING (IDR)",
        "TOTAL INCOMING (IDR)", "ENDING BALANCE (IDR)", "TOTAL:",
        "SAVINGS", "ACCOUNT", "S/N", "BANK STATEMENT"
    }
    
    i = 0
    while i < len(raw_lines):
        raw_line = raw_lines[i]
        stripped = raw_line.strip()
        
        # Skip header/footer lines
        if stripped.upper() in skip_keywords or re.match(r"^TOTAL:", stripped, re.IGNORECASE):
            i += 1
            continue
        
        m_date = date_line_re.match(raw_line)
        m_month = month_line_re.match(raw_line)
        
        if m_date:
            day = m_date.group(1).zfill(2)
            month = MONTH_MAP.get(m_date.group(2).lower(), "01")
            tx_date = f"{current_year}-{month}-{day}"
            
            # Extract amounts from THIS line (after the date part)
            # The column position determines if it's outgoing or incoming
            # Approx: outgoing is left-center, incoming is right
            after_date = raw_line[m_date.end():]
            
            # Find all amounts on this line
            amounts_found = list(re.finditer(r"([\d.,]+)", after_date))
            
            amount = 0.0
            amount_type = "credit"
            
            if amounts_found:
                # Use position on line as proxy for outgoing vs incoming column
                # The line is typically 80-100 chars wide
                # Outgoing: position < 60% of line length (left column)
                # Incoming: position > 60% of line length (right column)
                line_len = len(raw_line)
                for am in amounts_found:
                    abs_pos = m_date.end() + am.start()
                    val = clean_amount(am.group(1))
                    if val > 0:
                        amount = val
                        # Determine if outgoing or incoming based on column position
                        relative_pos = abs_pos / max(line_len, 1)
                        if relative_pos < 0.65:
                            # Left column = OUTGOING
                            amount_type = "debit"
                        else:
                            # Right column = INCOMING
                            amount_type = "credit"
                        break
            
            # Collect description from nearby lines
            desc_lines = []
            j = i + 1
            while j < len(raw_lines) and j < i + 5:
                nxt_raw = raw_lines[j]
                nxt = nxt_raw.strip()
                
                if not nxt:
                    j += 1
                    continue
                if date_line_re.match(nxt_raw) or month_line_re.match(nxt_raw):
                    break
                if nxt.upper() in skip_keywords:
                    j += 1
                    continue
                # Skip if it's a pure number line (another amount)
                if re.match(r"^[\d.,]+$", nxt):
                    j += 1
                    continue
                # Skip if line starts with a number pattern like "01 JUL 2024"
                if re.match(r"^\d{1,2}\s+[A-Z]{3}\s+\d{4}", nxt, re.IGNORECASE):
                    break
                desc_lines.append(nxt)
                j += 1
            
            desc = " ".join(desc_lines).strip()
            desc = re.sub(r"\s+", " ", desc)
            
            # Override amount_type from description keywords
            desc_lower = desc.lower()
            if any(k in desc_lower for k in ["payment", "tax", "pajak", "withdraw", "fee", "biaya", "bia"]):
                amount_type = "debit"
            elif any(k in desc_lower for k in ["interest", "bunga", "transfer from", "dari", "incoming"]):
                amount_type = "credit"
            
            if amount > 0:
                if amount_type == "credit":
                    incoming_total += amount
                else:
                    outgoing_total += amount
                
                transactions.append({
                    "transaction_date": tx_date,
                    "transaction_description": desc,
                    "transaction_amount": amount,
                    "amount_type": amount_type,
                    "transaction_bank": "SEABANK",
                    "transaction_balance": 0.0
                })
            
            i = j
            continue
        
        elif m_month:
            # Month-only header like "JUN" (for interest without day)
            month = MONTH_MAP.get(m_month.group(1).lower(), "01")
            tx_date = f"{current_year}-{month}-01"
            
            desc_lines = []
            amount = 0.0
            amount_type = "credit"
            
            # Look in this same line for amounts
            after_month = raw_line[m_month.end():]
            am_match = re.search(r"([\d.,]+)", after_month)
            if am_match:
                amount = clean_amount(am_match.group(1))
                # Same column heuristic
                abs_pos = m_month.end() + am_match.start()
                relative_pos = abs_pos / max(len(raw_line), 1)
                amount_type = "debit" if relative_pos < 0.65 else "credit"
            
            j = i + 1
            while j < len(raw_lines) and j < i + 5:
                nxt_raw = raw_lines[j]
                nxt = nxt_raw.strip()
                if not nxt:
                    j += 1
                    continue
                if date_line_re.match(nxt_raw) or month_line_re.match(nxt_raw):
                    break
                if nxt.upper() in skip_keywords:
                    j += 1
                    continue
                if re.match(r"^[\d.,]+$", nxt):
                    j += 1
                    continue
                desc_lines.append(nxt)
                j += 1
            
            desc = " ".join(desc_lines).strip()
            desc_lower = desc.lower()
            if any(k in desc_lower for k in ["tax", "pajak", "fee", "biaya"]):
                amount_type = "debit"
            elif any(k in desc_lower for k in ["interest", "bunga"]):
                amount_type = "credit"
            
            if amount > 0:
                if amount_type == "credit":
                    incoming_total += amount
                else:
                    outgoing_total += amount
                
                transactions.append({
                    "transaction_date": tx_date,
                    "transaction_description": desc,
                    "transaction_amount": amount,
                    "amount_type": amount_type,
                    "transaction_bank": "SEABANK",
                    "transaction_balance": 0.0
                })
            i = j
            continue
        
        i += 1

    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_total,
        "outgoing_transactions": outgoing_total,
        "transactions": transactions
    }


# ─────────────────────────────────────────────────────────────────────────────
# SMART ADAPTIVE PARSER (for truly unknown banks)
# ─────────────────────────────────────────────────────────────────────────────

def parse_smart(text: str, metadata: dict = None) -> dict:
    """
    An adaptive, signature-aware parser for bank statements that don't match
    known specific parsers. Uses header detection and flexible pattern matching.
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    
    period_val = "Unknown (Smart Parsed)"
    initial_balance = 0.0
    closing_balance = 0.0
    incoming_total = 0.0
    outgoing_total = 0.0
    transactions = []
    
    # Detect year
    year_match = re.search(r"202[0-9]", text)
    current_year = year_match.group(0) if year_match else str(datetime.now().year)
    
    # Detect period
    period_match = re.search(
        r"(Periode|Period|Range|dari|from)\s*[:]?\s*(.{5,60})",
        text, re.IGNORECASE
    )
    if period_match:
        period_val = period_match.group(2).strip()[:60]
    
    # Detect bank name for labeling
    bank_label = "UNKNOWN"
    bank_hints = {
        "BRI": r"BRI|Bank Rakyat Indonesia",
        "CIMB": r"CIMB|CIMB Niaga",
        "DANAMON": r"Danamon",
        "BTN": r"BTN|Bank Tabungan Negara",
        "OCBC": r"OCBC",
        "PERMATA": r"PermataBank|Bank Permata",
    }
    for b, pat in bank_hints.items():
        if re.search(pat, text, re.IGNORECASE):
            bank_label = b
            break
    
    # Detect summary fields
    for line in lines:
        if re.search(r"Saldo\s*Awal|Initial\s*Balance|Starting\s*Balance|STARTING BALANCE", line, re.IGNORECASE):
            m = re.search(r"([\d.,]+)", line)
            if m:
                initial_balance = clean_amount(m.group(1))
        if re.search(r"Saldo\s*Akhir|Closing\s*Balance|Ending\s*Balance|ENDING BALANCE", line, re.IGNORECASE):
            m = re.search(r"([\d.,]+)(?:\s|$)", line)
            if m:
                closing_balance = clean_amount(m.group(1))
    
    # Flexible transaction date patterns
    date_patterns = [
        # "DD MMM YYYY" or "DD MMM" 
        re.compile(r"^(\d{1,2})\s+(Jan(?:uari|uary)?|Feb(?:ruari|ruary)?|Mar(?:et|ch)?|Apr(?:il)?|Mei|May|Jun(?:i|e)?|Jul(?:i|y)?|Aug(?:ustus|ust)?|Agu(?:stus)?|Sep(?:tember)?|Okt(?:ober)?|Oct(?:ober)?|Nov(?:ember)?|Des(?:ember)?|Dec(?:ember)?)\s*(\d{4})?", re.IGNORECASE),
        # "DD/MM/YYYY" or "DD/MM"
        re.compile(r"^(\d{2})/(\d{2})(?:/(\d{4}))?"),
        # "DD-MM-YYYY"
        re.compile(r"^(\d{2})-(\d{2})-(\d{4})"),
    ]
    
    i = 0
    while i < len(lines):
        line = lines[i]
        
        matched_date = None
        pattern_type = None
        for p_idx, pat in enumerate(date_patterns):
            m = pat.match(line)
            if m:
                matched_date = m
                pattern_type = p_idx
                break
        
        if matched_date:
            day = matched_date.group(1).zfill(2)
            month_raw = matched_date.group(2)
            year = matched_date.group(3) if matched_date.lastindex >= 3 and matched_date.group(3) else current_year
            
            if month_raw.isdigit():
                month = month_raw.zfill(2)
            else:
                month = MONTH_MAP.get(month_raw.lower()[:3], "01")
            
            tx_date = f"{year}-{month}-{day}"
            
            # Extract amounts from the date line
            currency_vals = re.findall(
                r"([+-]?\s*\d{1,3}(?:[.,]\d{3})*[.,]\d{2})", line
            )
            
            # Description: everything after the date, before amounts
            remaining = line[matched_date.end():].strip()
            desc = remaining
            for v in currency_vals:
                desc = desc.replace(v, "")
            desc = re.sub(r"\s+", " ", desc).strip()
            
            # If description is empty, collect from next lines
            j = i + 1
            while j < len(lines) and j < i + 4:
                nxt = lines[j]
                # Stop if next line looks like a new date
                if any(pat.match(nxt) for pat in date_patterns):
                    break
                # Stop if next line is a pure number (balance line)
                if re.match(r"^[\d.,]+$", nxt):
                    break
                # Add to description if it's text
                if not re.match(r"^\d{2}:\d{2}", nxt):
                    desc = (desc + " " + nxt).strip()
                j += 1
            
            desc = re.sub(r"\s+", " ", desc).strip()
            
            # Parse amount and type
            amount = 0.0
            amount_type = "credit"
            balance = 0.0
            
            if len(currency_vals) >= 2:
                amount = abs(clean_amount(currency_vals[0]))
                balance = abs(clean_amount(currency_vals[-1]))
                if "-" in currency_vals[0] or "DB" in line.upper() or "DEBIT" in line.upper():
                    amount_type = "debit"
            elif len(currency_vals) == 1:
                amount = abs(clean_amount(currency_vals[0]))
                if "-" in currency_vals[0] or "DB" in line.upper():
                    amount_type = "debit"
            
            if amount > 0:
                if amount_type == "credit":
                    incoming_total += amount
                else:
                    outgoing_total += amount
                
                transactions.append({
                    "transaction_date": tx_date,
                    "transaction_description": desc,
                    "transaction_amount": amount,
                    "amount_type": amount_type,
                    "transaction_bank": bank_label,
                    "transaction_balance": balance
                })
        
        i += 1
    
    # Fallback: derive closing from last transaction
    if not closing_balance and transactions:
        closing_balance = transactions[-1]["transaction_balance"]
    
    return {
        "period": period_val,
        "initial_balance": initial_balance,
        "closing_balance": closing_balance,
        "incoming_transactions": incoming_total,
        "outgoing_transactions": outgoing_total,
        "transactions": transactions,
        "is_smart_parsed": True
    }


def clean_amount(amount_str: str) -> float:
    if not amount_str: 
        return 0.0
    
    # Keep digits, dots, commas, minus
    clean_str = re.sub(r"[^\d.,-]", "", str(amount_str)).strip()
    
    if not clean_str: 
        return 0.0

    # Handle negative sign
    is_negative = clean_str.startswith("-")
    clean_str = clean_str.lstrip("-")

    # Format detection: dot and comma both present
    if "." in clean_str and "," in clean_str:
        if clean_str.find(".") < clean_str.find(","):
            # Indonesian: 1.000.000,50 -> 1000000.50
            val = clean_str.replace(".", "").replace(",", ".")
        else:
            # US/BCA: 1,000.50 -> 1000.50
            val = clean_str.replace(",", "")
        return float(val) * (-1 if is_negative else 1)
    
    elif "," in clean_str:
        if re.search(r",\d{1,2}$", clean_str):
            # Comma is decimal separator: 1000,50
            val = clean_str.replace(",", ".")
        else:
            # Comma is thousands separator: 1,000,000
            val = clean_str.replace(",", "")
        return float(val) * (-1 if is_negative else 1)
    
    elif "." in clean_str:
        # Dots only: could be Indonesian thousands (20.152.067) or decimal (100.50)
        parts = clean_str.split(".")
        if len(parts) > 2:
            # Multiple dots = thousands separators: 20.152.067
            val = clean_str.replace(".", "")
        elif len(parts[-1]) == 2 or len(parts[-1]) == 3:
            # e.g. 100.50 (decimal) vs 100.000 (thousands)
            if len(parts[-1]) == 3:
                # Likely thousands: 100.000
                val = clean_str.replace(".", "")
            else:
                # Likely decimal: 100.50
                val = clean_str
        else:
            val = clean_str.replace(".", "")
        return float(val) * (-1 if is_negative else 1)
    
    # Pure integer
    try:
        return float(clean_str) * (-1 if is_negative else 1)
    except ValueError:
        return 0.0

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
