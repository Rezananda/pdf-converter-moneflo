
from api.parsers import parse_bank_statement
import json

def test_bca():
    print("Testing BCA Parser...")
    metadata = {"creator": "E-statement Batch Generator (PT. Bank Central Asia, Tbk)"}
    # Full Sample Text
    text = """
REKENING TAHAPAN
KCU SINGARAJA
MADE REZANANDA PUTRA 
BULELENG 
PEMARON 
BANJAR DINAS DAUH MARGI 
BULELENG 81116 
INDONESIA 
NO. REKENING
:
8270826602
HALAMAN
:
PERIODE
:
OKTOBER 2025
MATA UANG
:
IDR
CATATAN:
A p a b i l a  n a s a b a h  t i d a k  m e l a k u k a n  s a n g g a h a n  a t a s  L a p o r a n  M u t a s i  
Rekening ini sampai dengan akhir bulan berikutnya, nasabah dianggap 
telah menyetujui segala data yang tercantum pada Laporan Mutasi 
Rekening ini.
 •   
BCA berhak setiap saat melakukan koreksi apabila ada kesalahan pada 
Laporan Mutasi Rekening.
 •   
TANGGAL
KETERANGAN
CBG
MUTASI
SALDO
1 / 2
01/10
SALDO AWAL
1,045,271.93
07/10
TRSF E-BANKING DB 
0710/FTFVA/WS95271 
12608/SHOPEE 
- 
- 
1259672716 
135,700.00 DB
909,571.93
08/10
BI-FAST CR
BIF TRANSFER DR 
501 
Made Rezananda Put
135,700.00
1,045,271.93
10/10
TRSF E-BANKING DB 
TANGGAL :09/10
0910/FTFVA/WS95271 
12608/SHOPEE 
- 
- 
1259672716 
150,910.00 DB
10/10
TRSF E-BANKING DB 
1010/FTFVA/WS95271 
12608/SHOPEE 
- 
- 
1259672716 
243,970.00 DB
10/10
TRANSAKSI DEBIT 
TGL: 10/10 
QR 008 
00000.00telor gulu
5,000.00 DB
10/10
TRANSAKSI DEBIT 
TGL: 10/10 
QR 008 
00000.00Ayam Penye
69,000.00 DB
10/10
TRANSAKSI DEBIT 
TGL: 10/10 
QRC014 
00000.00IDM INDOMA
39,900.00 DB
536,491.93
11/10
TRANSAKSI DEBIT 
TGL: 11/10 
QRC014 
00000.00IDM INDOMA
25,000.00 DB
511,491.93
12/10
BI-FAST CR
BIF TRANSFER DR 
501 
Made Rezananda Put
538,509.00
1,050,000.93
16/10
TRSF E-BANKING DB 
1610/FTFVA/WS95271 
12608/SHOPEE 
- 
- 
1259672716 
98,332.00 DB
951,668.93
Bersambung ke halaman berikut
SALDO AWAL
:
1,045,271.93
MUTASI CR
:
674,209.00
2
MUTASI DB
:
1,474,309.00
18
SALDO AKHIR
:
245,171.93
    """
    try:
        results = parse_bank_statement(text, metadata, "bca_real_test.pdf")
        print(json.dumps(results, indent=2))
        print(f"BCA Transaction Count: {len(results['transactions'])}")
    except Exception as e:
        print(f"BCA Error: {e}")

def test_mandiri():
    print("\nTesting Mandiri Parser...")
    metadata = {"creator": "PT. Bank Mandiri (Persero) Tbk"}
    text = """
(OJK) dan Bank Indonesia (BI),
serta merupakan peserta penjamin Lembaga Penjamin Simpanan (LPS)
Mandiri Call 14000

Plaza Mandiri. Jl. Jend. Gatot Subroto Kav. 36-38. Jakarta
e-Statement
Nama/Name
Cabang/Branch
:
:
Dicetak pada/Issued on
Periode/Period
:
:
MADE REZANANDA PUTRA
KCP Jakarta Mall Ciputra
01 Nov 2025 - 30 Nov 2025
16 Dec 2025
3 of 11
 11
3 dari
No
No
Date
Tanggal
Saldo (IDR)
Balance (IDR)
Nominal (IDR)
Amount (IDR)
Keterangan
Remarks
Pembayaran QR
ke IDM QRIS LIVIN
531119289123
466.850,72
23
-28.800,00
19:14:48 WIB
07 Nov 2025
Penarikan tunai tanpa kartu
Di ATM KUT IM MENJANGAN 01
081259672716
166.850,72
24
-300.000,00
15:44:04 WIB
08 Nov 2025
Transfer BI Fast
Dari BANK DIGITAL BCA
Made Rezananda Putra 000000005122
-
Tabungan NOW IDR
Saldo Awal/Initial Balance

:

216.600,72

Nomor Rekening/Account Number :

1170010426534

Dana Masuk/Incoming Transactions

:

+ 35.419.333,00

Mata Uang/Currency

IDR

Dana Keluar/Outgoing Transactions

:

- 35.253.551,00

Saldo Akhir/Closing Balance

:

382.382,72

No
No
    """
    try:
        results = parse_bank_statement(text, metadata, "mandiri_real_test.pdf")
        print(json.dumps(results, indent=2))
        print(f"Mandiri Transaction Count: {len(results['transactions'])}")
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
