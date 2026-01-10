
from api.parsers import parse_bank_statement
import json

def test_bca():
    print("Testing BCA Parser...")
    metadata = {"creator": "E-statement Batch Generator (PT. Bank Central Asia, Tbk)"}
    # Adjusted layout: Date -> Description -> Amount
    text = """
PERIODE: OKTOBER 2024
SALDO AWAL 5,000,000.00

25/11
TRANSAKSI DEBIT KFC
16,000.00
TGL: 25/11

26/11
BIAYA ADMIN
500.00 DB
    """
    try:
        results = parse_bank_statement(text, metadata)
        print(json.dumps(results, indent=2))
    except Exception as e:
        print(f"BCA Error: {e}")

def test_mandiri():
    print("\nTesting Mandiri Parser...")
    metadata = {"creator": "PT. Bank Mandiri (Persero) Tbk"}
    text = """
Transfer to Bob
-50.000,00
01 Nov 2025

Salary from Boss
+1.000.000,00
02 Nov 2025
    """
    try:
        results = parse_bank_statement(text, metadata)
        print(json.dumps(results, indent=2))
    except Exception as e:
        print(f"Mandiri Error: {e}")

def test_unsupported():
    print("\nTesting Unsupported Bank...")
    metadata = {"creator": "Unknown Bank"}
    try:
        parse_bank_statement("some text", metadata)
    except ValueError as e:
        print(f"Caught expected error: {e}")

if __name__ == "__main__":
    test_bca()
    test_mandiri()
    test_unsupported()
