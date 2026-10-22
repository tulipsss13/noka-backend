import os
import json
import time
import tempfile
from datetime import date, timedelta

from fastapi import (
    FastAPI,
    Depends,
    UploadFile,
    File,
    HTTPException,
)
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from faster_whisper import WhisperModel
from google import genai

from database import engine, Base, SessionLocal
import models
from security import hash_password, verify_password
from auth import create_access_token, decode_access_token


# =========================
# KONFIGURASI AWAL
# =========================

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="NOKA Backend")
security = HTTPBearer()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://noka-nota-kas.vercel.app"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = (
    genai.Client(api_key=GEMINI_API_KEY)
    if GEMINI_API_KEY
    else None
)

# Model Whisper dimuat satu kali saat backend dijalankan.
model = WhisperModel(
    "base",
    device="cpu",
    compute_type="int8",
)

# Penyimpanan sementara untuk demo reset password.
reset_verified_emails = set()


# =========================
# MODEL REQUEST
# =========================

class TransactionRequest(BaseModel):
    jenis: str
    jumlah: int
    kategori: str
    keterangan: str
    tanggal: str


# =========================
# FUNGSI BANTU
# =========================

def get_user_id(credentials: HTTPAuthorizationCredentials) -> int:
    """Memeriksa token dan mengambil ID pengguna."""
    try:
        data = decode_access_token(credentials.credentials)

        if not data or "user_id" not in data:
            raise HTTPException(
                status_code=401,
                detail="Token tidak valid.",
            )

        return data["user_id"]

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="Token tidak valid atau kedaluwarsa.",
        )


def proses_dengan_gemini(teks: str) -> dict:
    """Mengubah teks suara menjadi data transaksi."""
    if not client:
        raise HTTPException(
            status_code=500,
            detail="GEMINI_API_KEY belum dikonfigurasi.",
        )

    prompt = f"""
Kamu adalah sistem pencatat keuangan UMKM.

Ubah kalimat transaksi berikut menjadi JSON valid.

Aturan:
- jenis harus "pemasukan" atau "pengeluaran"
- jumlah harus berupa bilangan bulat, tanpa simbol mata uang
- kategori harus salah satu dari "Penjualan", "Bahan",
  "Operasional", atau "Lainnya"
- keterangan harus singkat dan jelas
- jangan menebak jumlah jika tidak disebutkan;
  jika jumlah tidak jelas, gunakan 0
- jangan memberikan penjelasan atau markdown

Format JSON:
{{
  "jenis": "pengeluaran",
  "jumlah": 30000,
  "kategori": "Bahan",
  "keterangan": "beli beras"
}}

Kalimat pengguna:
{teks}
"""

    try:
        response = client.models.generate_content(
            model="gemini-3.5-flash-lite",
            contents=prompt,
        )

        if not response.text:
            raise ValueError("Respons Gemini kosong.")

        hasil = response.text.strip()
        hasil = hasil.replace("```json", "").replace("```", "").strip()

        data = json.loads(hasil)

        jenis = str(data.get("jenis", "")).lower()
        if jenis not in ("pemasukan", "pengeluaran"):
            raise ValueError("Jenis transaksi tidak valid.")

        jumlah = int(data.get("jumlah", 0))
        if jumlah < 0:
            raise ValueError("Jumlah transaksi tidak boleh negatif.")

        kategori = str(data.get("kategori", "Lainnya"))
        kategori_valid = [
            "Penjualan",
            "Bahan",
            "Operasional",
            "Lainnya",
        ]

        if kategori not in kategori_valid:
            kategori = "Lainnya"

        return {
            "jenis": jenis,
            "jumlah": jumlah,
            "kategori": kategori,
            "keterangan": str(data.get("keterangan", teks)),
        }

    except HTTPException:
        raise
    except Exception as e:
        print(f"Kesalahan Gemini: {e}")
        raise HTTPException(
            status_code=502,
            detail="Gagal memproses transaksi dengan Gemini.",
        )


def transkripsikan_audio(audio_path: str) -> str:
    """Mengubah file audio menjadi teks bahasa Indonesia."""
    try:
        segments, info = model.transcribe(
            audio_path,
            language="id",
            beam_size=1,
        )

        # segments bersifat generator, jadi harus dibaca.
        teks = "".join(segment.text for segment in segments).strip()

        if not teks:
            raise HTTPException(
                status_code=422,
                detail="Suara tidak berhasil dikenali. Coba bicara lebih jelas.",
            )

        return teks

    except HTTPException:
        raise
    except Exception as e:
        print(f"Kesalahan Whisper: {e}")
        raise HTTPException(
            status_code=500,
            detail="Gagal mengubah audio menjadi teks.",
        )


# =========================
# BERANDA
# =========================

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
        user_lama = (
            db.query(models.User)
            .filter(models.User.email == email)
            .first()
        )

        if user_lama:
            raise HTTPException(
                status_code=400,
                detail="Email sudah terdaftar.",
            )

        user = models.User(
            nama=nama,
            email=email,
            password=hash_password(password),
            terverifikasi=0,
        )

        db.add(user)
        db.commit()

        kode_otp = "1234"

        otp = models.OTP(
            email=email,
            kode=kode_otp,
        )

        db.add(otp)
        db.commit()

        return {
            "message": "Registrasi berhasil! OTP telah dibuat.",
            "email": email,
            "otp": kode_otp,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Kesalahan register: {e}")
        raise HTTPException(
            status_code=500,
            detail="Registrasi gagal.",
        )
    finally:
        db.close()


# =========================
# VERIFY OTP REGISTRASI
# =========================

@app.post("/verify-otp")
def verify_otp(email: str, kode: str):
    db = SessionLocal()

    try:
        otp = (
            db.query(models.OTP)
            .filter(
                models.OTP.email == email,
                models.OTP.kode == kode,
            )
            .first()
        )

        if not otp:
            return {"message": "OTP salah!"}

        user = (
            db.query(models.User)
            .filter(models.User.email == email)
            .first()
        )

        if not user:
            return {"message": "Email tidak ditemukan."}

        user.terverifikasi = 1
        db.delete(otp)
        db.commit()

        return {
            "message": "OTP benar! Verifikasi berhasil."
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
        user = (
            db.query(models.User)
            .filter(models.User.email == email)
            .first()
        )

        if not user or not verify_password(password, user.password):
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
            "access_token": token,
        }

    finally:
        db.close()


# =========================
# TAMBAH TRANSAKSI
# =========================

@app.post("/transaksi")
def tambah_transaksi(
    body: TransactionRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_id = get_user_id(credentials)
    db = SessionLocal()

    try:
        if body.jenis not in ("pemasukan", "pengeluaran"):
            raise HTTPException(
                status_code=400,
                detail="Jenis transaksi tidak valid.",
            )

        if body.jumlah < 0:
            raise HTTPException(
                status_code=400,
                detail="Jumlah tidak boleh negatif.",
            )

        transaksi = models.Transaction(
            user_id=user_id,
            jenis=body.jenis,
            jumlah=body.jumlah,
            kategori=body.kategori,
            keterangan=body.keterangan,
            tanggal=body.tanggal,
        )

        db.add(transaksi)
        db.commit()

        return {
            "message": "Transaksi berhasil disimpan!"
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"Kesalahan tambah transaksi: {e}")
        raise HTTPException(
            status_code=500,
            detail="Gagal menyimpan transaksi.",
        )
    finally:
        db.close()


# =========================
# LIHAT TRANSAKSI
# =========================

@app.get("/transaksi")
def lihat_transaksi(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_id = get_user_id(credentials)
    db = SessionLocal()

    try:
        transaksi = (
            db.query(models.Transaction)
            .filter(models.Transaction.user_id == user_id)
            .all()
        )

        return [
            {
                "id": item.id,
                "jenis": item.jenis,
                "jumlah": item.jumlah,
                "kategori": item.kategori,
                "keterangan": item.keterangan,
                "tanggal": item.tanggal,
            }
            for item in transaksi
        ]

    finally:
        db.close()


# =========================
# LIHAT SALDO
# =========================

@app.get("/saldo")
def lihat_saldo(
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_id = get_user_id(credentials)
    db = SessionLocal()

    try:
        transaksi = (
            db.query(models.Transaction)
            .filter(models.Transaction.user_id == user_id)
            .all()
        )

        total_pemasukan = sum(
            item.jumlah
            for item in transaksi
            if item.jenis == "pemasukan"
        )

        total_pengeluaran = sum(
            item.jumlah
            for item in transaksi
            if item.jenis == "pengeluaran"
        )

        return {
            "total_pemasukan": total_pemasukan,
            "total_pengeluaran": total_pengeluaran,
            "saldo": total_pemasukan - total_pengeluaran,
        }

    finally:
        db.close()


# =========================
# DASHBOARD
# =========================

@app.get("/dashboard")
def dashboard(
    filter: str = "harian",
    credentials: HTTPAuthorizationCredentials = Depends(security),
):
    user_id = get_user_id(credentials)
    db = SessionLocal()

    try:
        user = (
            db.query(models.User)
            .filter(models.User.id == user_id)
            .first()
        )

        if not user:
            raise HTTPException(
                status_code=404,
                detail="User tidak ditemukan.",
            )

        semua_transaksi = (
            db.query(models.Transaction)
            .filter(models.Transaction.user_id == user_id)
            .all()
        )

        hari_ini = date.today()
        tanggal_hari_ini = hari_ini.isoformat()

        if filter == "harian":
            transaksi = [
                item for item in semua_transaksi
                if item.tanggal == tanggal_hari_ini
            ]

        elif filter == "mingguan":
            awal_minggu = hari_ini - timedelta(
                days=hari_ini.weekday()
            )

            transaksi = []

            for item in semua_transaksi:
                try:
                    tanggal_transaksi = date.fromisoformat(item.tanggal)

                    if awal_minggu <= tanggal_transaksi <= hari_ini:
                        transaksi.append(item)
                except (ValueError, TypeError):
                    continue

        elif filter == "bulanan":
            transaksi = []

            for item in semua_transaksi:
                try:
                    tanggal_transaksi = date.fromisoformat(item.tanggal)

                    if (
                        tanggal_transaksi.year == hari_ini.year
                        and tanggal_transaksi.month == hari_ini.month
                    ):
                        transaksi.append(item)
                except (ValueError, TypeError):
                    continue

        else:
            raise HTTPException(
                status_code=400,
                detail="Filter harus harian, mingguan, atau bulanan.",
            )

        total_pemasukan = sum(
            item.jumlah for item in transaksi
            if item.jenis == "pemasukan"
        )

        total_pengeluaran = sum(
            item.jumlah for item in transaksi
            if item.jenis == "pengeluaran"
        )

        total_semua_pemasukan = sum(
            item.jumlah for item in semua_transaksi
            if item.jenis == "pemasukan"
        )

        total_semua_pengeluaran = sum(
            item.jumlah for item in semua_transaksi
            if item.jenis == "pengeluaran"
        )

        saldo = total_semua_pemasukan - total_semua_pengeluaran

        pemasukan_hari_ini = sum(
            item.jumlah for item in semua_transaksi
            if (
                item.tanggal == tanggal_hari_ini
                and item.jenis == "pemasukan"
            )
        )

        pengeluaran_hari_ini = sum(
            item.jumlah for item in semua_transaksi
            if (
                item.tanggal == tanggal_hari_ini
                and item.jenis == "pengeluaran"
            )
        )

        data_grafik = {}

        for item in transaksi:
            tanggal = item.tanggal

            if tanggal not in data_grafik:
                data_grafik[tanggal] = {
                    "tanggal": tanggal,
                    "pemasukan": 0,
                    "pengeluaran": 0,
                }

            if item.jenis == "pemasukan":
                data_grafik[tanggal]["pemasukan"] += item.jumlah
            elif item.jenis == "pengeluaran":
                data_grafik[tanggal]["pengeluaran"] += item.jumlah

        grafik = sorted(
            data_grafik.values(),
            key=lambda item: item["tanggal"],
        )

        data_kategori = {}

        for item in transaksi:
            kategori = item.kategori

            if kategori not in data_kategori:
                data_kategori[kategori] = 0

            data_kategori[kategori] += item.jumlah

        kategori_list = [
            {"kategori": kategori, "jumlah": jumlah}
            for kategori, jumlah in data_kategori.items()
        ]

        kategori_list.sort(
            key=lambda item: item["jumlah"],
            reverse=True,
        )

        riwayat = [
            {
                "id": item.id,
                "jenis": item.jenis,
                "jumlah": item.jumlah,
                "kategori": item.kategori,
                "keterangan": item.keterangan,
                "tanggal": item.tanggal,
            }
            for item in transaksi
        ]

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
            "riwayat_transaksi": riwayat,
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
        user = (
            db.query(models.User)
            .filter(models.User.email == email)
            .first()
        )

        if not user:
            return {"message": "Email tidak ditemukan."}

        kode_otp = "1234"

        otp = models.OTP(
            email=email,
            kode=kode_otp,
        )

        db.add(otp)
        db.commit()

        return {
            "message": "OTP reset password berhasil dibuat.",
            "email": email,
            "otp": kode_otp,
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
        otp = (
            db.query(models.OTP)
            .filter(
                models.OTP.email == email,
                models.OTP.kode == kode,
            )
            .first()
        )

        if otp:
            reset_verified_emails.add(email)

            return {
                "message": "OTP benar! Silakan buat kata sandi baru."
            }

        return {"message": "OTP salah!"}

    finally:
        db.close()


# =========================
# RESET PASSWORD
# =========================

@app.post("/reset-password")
def reset_password(
    email: str,
    password_baru: str,
    konfirmasi_password: str,
):
    db = SessionLocal()

    try:
        if email not in reset_verified_emails:
            return {
                "message": "Silakan verifikasi OTP terlebih dahulu."
            }

        user = (
            db.query(models.User)
            .filter(models.User.email == email)
            .first()
        )

        if not user:
            return {"message": "Email tidak ditemukan."}

        if password_baru != konfirmasi_password:
            return {"message": "Password tidak sama."}

        user.password = hash_password(password_baru)
        db.commit()

        reset_verified_emails.discard(email)

        return {"message": "Password berhasil diubah."}

    finally:
        db.close()


# =========================
# SPEECH TO TEXT
# =========================

@app.post("/speech-to-text")
async def speech_to_text(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    audio_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp_file:
            audio_path = temp_file.name
            temp_file.write(await file.read())

        teks = transkripsikan_audio(audio_path)

        return {"text": teks}

    finally:
        await file.close()

        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)


# =========================
# TEST GEMINI
# =========================

@app.post("/test-gemini")
def test_gemini(text: str):
    return proses_dengan_gemini(text)


# =========================
# PROSES SUARA
# =========================

@app.post("/proses-suara")
async def proses_suara(file: UploadFile = File(...)):
    waktu_mulai = time.perf_counter()
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    audio_path = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False,
            suffix=suffix,
        ) as temp_file:
            audio_path = temp_file.name
            temp_file.write(await file.read())

        waktu_whisper = time.perf_counter()
        teks = transkripsikan_audio(audio_path)

        print(
            f"Waktu Whisper: "
            f"{time.perf_counter() - waktu_whisper:.2f} detik"
        )

        waktu_gemini = time.perf_counter()
        data = proses_dengan_gemini(teks)

        print(
            f"Waktu Gemini: "
            f"{time.perf_counter() - waktu_gemini:.2f} detik"
        )

        print(
            f"Total waktu proses suara: "
            f"{time.perf_counter() - waktu_mulai:.2f} detik"
        )

        return {
            "teks": teks,
            "transaksi": data,
        }

    finally:
        await file.close()

        if audio_path and os.path.exists(audio_path):
            os.remove(audio_path)

