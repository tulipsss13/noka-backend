from fastapi import FastAPI, Depends
from pydantic import BaseModel
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from database import engine, Base, SessionLocal
import models
from security import hash_password, verify_password
from auth import create_access_token, decode_access_token
from fastapi.middleware.cors import CORSMiddleware

import whisper
from fastapi import UploadFile, File

from google import genai
import os
import shutil
import json
from dotenv import load_dotenv

Base.metadata.create_all(bind=engine)

app = FastAPI()
security = HTTPBearer()

class TransactionRequest(BaseModel):
    jenis: str
    jumlah: int
    kategori: str
    keterangan: str
    tanggal: str

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
model = whisper.load_model("small")
load_dotenv()
FFMPEG_PATH = r"C:\Users\zhara\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg.Shared_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build-shared\bin"

os.environ["PATH"] += os.pathsep + FFMPEG_PATH
client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)
reset_verified_emails = set()

@app.get("/")
def home():
    return {
        "message": "NOKA Backend berhasil dijalankan!"
    }


# =========================
# REGISTER
# =========================

@app.post("/register")
def register(nama: str, email: str, password: str):
    db = SessionLocal()

    try:
        user = models.User(
            nama=nama,
            email=email,
            password=hash_password(password),
            terverifikasi=0
        )

        db.add(user)
        db.commit()

        kode_otp = "1234"

        otp = models.OTP(
            email=email,
            kode=kode_otp
        )

        db.add(otp)
        db.commit()

        return {
            "message": "Registrasi berhasil! OTP telah dibuat.",
            "email": email,
            "otp": kode_otp
        }

    except Exception as e:
        db.rollback()

        return {
            "error": str(e)
        }

    finally:
        db.close()


# =========================
# VERIFY OTP
# =========================

@app.post("/verify-otp")
def verify_otp(email: str, kode: str):
    db = SessionLocal()

    try:
        otp = db.query(models.OTP).filter(
            models.OTP.email == email,
            models.OTP.kode == kode
        ).first()

        if otp:
            user = db.query(models.User).filter(
                models.User.email == email
            ).first()

            if user:
                user.terverifikasi = 1
                db.commit()

                return {
                    "message": "OTP benar! Verifikasi berhasil."
                }

        return {
            "message": "OTP salah!"
        }

    finally:
        db.close()


# =========================
# LOGIN
# =========================

@app.post("/login")
def login(email: str, password: str):
    db = SessionLocal()

    try:
        user = db.query(models.User).filter(
            models.User.email == email
        ).first()

        if not user:
            return {
                "message": "Email atau password salah."
            }

        if not verify_password(password, user.password):
            return {
                "message": "Email atau password salah."
            }

        if user.terverifikasi == 0:
            return {
                "message": "Akun belum terverifikasi."
            }

        token = create_access_token(user.id)

        return {
            "message": "Login berhasil!",
            "nama": user.nama,
            "email": user.email,
            "access_token": token
        }

    finally:
        db.close()


# =========================
# TAMBAH TRANSAKSI
# =========================

@app.post("/transaksi")
def tambah_transaksi(
    body: TransactionRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    db = SessionLocal()
    try:
        token = credentials.credentials
        data = decode_access_token(token)
        user_id = data["user_id"]

        transaksi = models.Transaction(
            user_id=user_id,
            jenis=body.jenis,
            jumlah=body.jumlah,
            kategori=body.kategori,
            keterangan=body.keterangan,
            tanggal=body.tanggal
        )

        db.add(transaksi)
        db.commit()

        return {
            "message": "Transaksi berhasil disimpan!"
        }
    finally:
        db.close()

# =========================
# LIHAT TRANSAKSI
# =========================
@app.get("/transaksi")
def lihat_transaksi(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    db = SessionLocal()
    try:
        token = credentials.credentials
        data = decode_access_token(token)
        user_id = data["user_id"]

        transaksi = db.query(models.Transaction).filter(
            models.Transaction.user_id == user_id
        ).all()

        hasil = []

        for item in transaksi:
            hasil.append({
                "id": item.id,
                "jenis": item.jenis,
                "jumlah": item.jumlah,
                "kategori": item.kategori,
                "keterangan": item.keterangan,
                "tanggal": item.tanggal
            })

        return hasil

    finally:
        db.close()

# =========================
# LIHAT SALDO
# =========================
@app.get("/saldo")
def lihat_saldo(
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    db = SessionLocal()
    try:
        token = credentials.credentials
        data = decode_access_token(token)
        user_id = data["user_id"]

        transaksi = db.query(models.Transaction).filter(
            models.Transaction.user_id == user_id
        ).all()

        total_pemasukan = 0
        total_pengeluaran = 0

        for item in transaksi:
            if item.jenis == "pemasukan":
                total_pemasukan += item.jumlah
            elif item.jenis == "pengeluaran":
                total_pengeluaran += item.jumlah

        saldo = total_pemasukan - total_pengeluaran

        return {
            "total_pemasukan": total_pemasukan,
            "total_pengeluaran": total_pengeluaran,
            "saldo": saldo
        }

    finally:
        db.close()

# =========================
# DASHBOARD
# =========================
@app.get("/dashboard")
def dashboard(
    filter: str = "harian",
    credentials: HTTPAuthorizationCredentials = Depends(security)
):
    db = SessionLocal()
    try:
        # =========================
        # CEK TOKEN
        # =========================
        token = credentials.credentials
        data = decode_access_token(token)
        user_id = data["user_id"]

        # =========================
        # AMBIL DATA USER
        # =========================
        user = db.query(models.User).filter(
            models.User.id == user_id
        ).first()

        if not user:
            return {
                "error": "User tidak ditemukan."
            }

        # =========================
        # AMBIL SEMUA TRANSAKSI USER
        # =========================
        semua_transaksi = db.query(models.Transaction).filter(
            models.Transaction.user_id == user_id
        ).all()

        # =========================
        # TANGGAL
        # =========================
        from datetime import date, timedelta

        hari_ini = date.today()
        tanggal_hari_ini = hari_ini.isoformat()

        # =========================
        # FILTER DATA
        # =========================
        transaksi = []

        if filter == "harian":

            for item in semua_transaksi:
                if item.tanggal == tanggal_hari_ini:
                    transaksi.append(item)

        elif filter == "mingguan":

            awal_minggu = hari_ini - timedelta(
                days=hari_ini.weekday()
            )

            for item in semua_transaksi:
                try:
                    tanggal_transaksi = date.fromisoformat(
                        item.tanggal
                    )

                    if (
                        awal_minggu
                        <= tanggal_transaksi
                        <= hari_ini
                    ):
                        transaksi.append(item)

                except:
                    pass

        elif filter == "bulanan":

            for item in semua_transaksi:
                try:
                    tanggal_transaksi = date.fromisoformat(
                        item.tanggal
                    )

                    if (
                        tanggal_transaksi.year == hari_ini.year
                        and tanggal_transaksi.month == hari_ini.month
                    ):
                        transaksi.append(item)

                except:
                    pass

        else:
            return {
                "error": "Filter harus harian, mingguan, atau bulanan."
            }

        # =========================
        # TOTAL PEMASUKAN
        # SESUAI FILTER
        # =========================
        total_pemasukan = 0

        for item in transaksi:
            if item.jenis == "pemasukan":
                total_pemasukan += item.jumlah

        # =========================
        # TOTAL PENGELUARAN
        # SESUAI FILTER
        # =========================
        total_pengeluaran = 0

        for item in transaksi:
            if item.jenis == "pengeluaran":
                total_pengeluaran += item.jumlah

        # =========================
        # SALDO KESELURUHAN
        # TIDAK TERPENGARUH FILTER
        # =========================
        total_semua_pemasukan = 0
        total_semua_pengeluaran = 0

        for item in semua_transaksi:

            if item.jenis == "pemasukan":
                total_semua_pemasukan += item.jumlah

            elif item.jenis == "pengeluaran":
                total_semua_pengeluaran += item.jumlah

        saldo = (
            total_semua_pemasukan
            - total_semua_pengeluaran
        )

        # =========================
        # PEMASUKAN HARI INI
        # =========================
        pemasukan_hari_ini = 0

        # =========================
        # PENGELUARAN HARI INI
        # =========================
        pengeluaran_hari_ini = 0

        for item in semua_transaksi:

            if item.tanggal == tanggal_hari_ini:

                if item.jenis == "pemasukan":
                    pemasukan_hari_ini += item.jumlah

                elif item.jenis == "pengeluaran":
                    pengeluaran_hari_ini += item.jumlah

        # =========================
        # DATA GRAFIK
        # SESUAI FILTER
        # =========================
        data_grafik = {}

        for item in transaksi:

            tanggal = item.tanggal

            if tanggal not in data_grafik:
                data_grafik[tanggal] = {
                    "tanggal": tanggal,
                    "pemasukan": 0,
                    "pengeluaran": 0
                }

            if item.jenis == "pemasukan":
                data_grafik[tanggal]["pemasukan"] += item.jumlah

            elif item.jenis == "pengeluaran":
                data_grafik[tanggal]["pengeluaran"] += item.jumlah

        grafik = list(data_grafik.values())

        grafik.sort(
            key=lambda x: x["tanggal"]
        )

        # =========================
        # DATA BERDASARKAN KATEGORI
        # SESUAI FILTER
        # =========================
        data_kategori = {}

        for item in transaksi:

            kategori = item.kategori

            if kategori not in data_kategori:
                data_kategori[kategori] = 0

            data_kategori[kategori] += item.jumlah

        # =========================
        # UBAH DATA KATEGORI
        # MENJADI LIST
        # =========================
        kategori_list = []

        for kategori, jumlah in data_kategori.items():

            kategori_list.append({
                "kategori": kategori,
                "jumlah": jumlah
            })

        # =========================
        # URUTKAN KATEGORI
        # DARI JUMLAH TERBESAR
        # =========================
        kategori_list.sort(
            key=lambda x: x["jumlah"],
            reverse=True
        )

        # =========================
        # RIWAYAT TRANSAKSI
        # SESUAI FILTER
        # =========================
        riwayat = []

        for item in transaksi:

            riwayat.append({
                "id": item.id,
                "jenis": item.jenis,
                "jumlah": item.jumlah,
                "kategori": item.kategori,
                "keterangan": item.keterangan,
                "tanggal": item.tanggal
            })

        # =========================
        # RESPONSE DASHBOARD
        # =========================
        return {
            "nama": user.nama,
            "filter": filter,
            "total_pemasukan": total_pemasukan,
            "total_pengeluaran": total_pengeluaran,
            "saldo": saldo,
            "pemasukan_hari_ini": pemasukan_hari_ini,
            "pengeluaran_hari_ini": pengeluaran_hari_ini,
            "data_grafik": grafik,
            "data_kategori": kategori_list,
            "riwayat_transaksi": riwayat
        }

    except Exception as e:
        return {
            "error": str(e)
        }

    finally:
        db.close()

# =========================
# FORGOT PASSWORD
# =========================

@app.post("/forgot-password")
def forgot_password(email: str):
    db = SessionLocal()

    try:
        user = db.query(models.User).filter(
            models.User.email == email
        ).first()

        if not user:
            return {
                "message": "Email tidak ditemukan."
            }

        kode_otp = "1234"

        otp = models.OTP(
            email=email,
            kode=kode_otp
        )

        db.add(otp)
        db.commit()

        return {
            "message": "OTP reset password berhasil dibuat.",
            "email": email,
            "otp": kode_otp
        }

    finally:
        db.close()

# =========================
# VERIFY OTP RESET PASSWORD
# =========================

@app.post("/verify-reset-otp")
def verify_reset_otp(email: str, kode: str):
    db = SessionLocal()

    try:
        otp = db.query(models.OTP).filter(
            models.OTP.email == email,
            models.OTP.kode == kode
        ).first()

        if otp:
            reset_verified_emails.add(email)

            return {
                "message": "OTP benar! Silakan buat kata sandi baru."
            }

        return {
            "message": "OTP salah!"
        }

    finally:
        db.close()

# =========================
# RESET PASSWORD
# =========================

@app.post("/reset-password")
def reset_password(
    email: str,
    password_baru: str,
    konfirmasi_password: str
):
    db = SessionLocal()

    try:
        # Cek apakah OTP reset sudah diverifikasi
        if email not in reset_verified_emails:
            return {
                "message": "Silakan verifikasi OTP terlebih dahulu."
            }

        # Cek apakah email terdaftar
        user = db.query(models.User).filter(
            models.User.email == email
        ).first()

        if not user:
            return {
                "message": "Email tidak ditemukan."
            }

        # Cek password baru dan konfirmasi
        if password_baru != konfirmasi_password:
            return {
                "message": "Password tidak sama."
            }

        # Ubah password
        hashed_password = hash_password(password_baru)

        user.password = hashed_password

        db.commit()

        # Hapus status verifikasi setelah password berhasil diubah
        reset_verified_emails.remove(email)

        return {
            "message": "Password berhasil diubah."
        }

    finally:
        db.close()

# =========================
# SPEECH TO TEXT
# =========================

@app.post("/speech-to-text")
async def speech_to_text(file: UploadFile = File(...)):
    audio_data = await file.read()

    with open("temp_audio", "wb") as f:
        f.write(audio_data)

    result = model.transcribe("temp_audio", language="id")

    return {
        "text": result["text"]
    }

# =========================
# TEST GEMINI
# =========================
@app.post("/test-gemini")
def test_gemini(text: str):

    prompt = f"""
Kamu adalah sistem pencatat keuangan UMKM.

Tugas kamu adalah mengubah kalimat transaksi pengguna menjadi data transaksi.

Tentukan:
- jenis: "pemasukan" atau "pengeluaran"
- jumlah: angka saja
- kategori: kategori transaksi
- keterangan: penjelasan singkat transaksi

Jangan bertanya balik.
Jangan memberikan penjelasan tambahan.
Hanya berikan JSON valid tanpa markdown dan tanpa ```.

Contoh:
{{
  "jenis": "pengeluaran",
  "jumlah": 30000,
  "kategori": "Bahan",
  "keterangan": "beli beras"
}}

Kalimat pengguna:
{text}
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt
    )

    hasil = response.text.strip()

    # Menghapus ```json dan ``` jika Gemini masih memberikannya
    hasil = hasil.replace("```json", "").replace("```", "").strip()

    data = json.loads(hasil)

    return data

# =========================
# PROSES SUARA
# =========================

@app.post("/proses-suara")
async def proses_suara(file: UploadFile = File(...)):

    audio_data = await file.read()

    with open("temp_audio.webm", "wb") as f:
        f.write(audio_data)

    result = model.transcribe(
        "temp_audio.webm",
        language="id"
    )

    teks = result["text"]

    prompt = f"""
Kamu adalah sistem pencatat keuangan untuk UMKM.

Ubah kalimat transaksi berikut menjadi JSON.

Tentukan:
- jenis: "pemasukan" atau "pengeluaran"
- jumlah: angka saja
- kategori: pilih salah satu dari:
  "Penjualan", "Bahan", "Operasional", "Lainnya"
- keterangan: penjelasan singkat transaksi

Jangan bertanya kepada pengguna.
Jangan memberikan penjelasan tambahan.
Jangan menggunakan markdown.
Hanya berikan JSON yang valid.

Kalimat transaksi:
{teks}
"""

    response = client.models.generate_content(
        model="gemini-3.5-flash-lite",
        contents=prompt
    )

    hasil = response.text.strip()

    hasil = hasil.replace("```json", "").replace("```", "").strip()

    data = json.loads(hasil)

    return {
        "teks": teks,
        "transaksi": data
    }