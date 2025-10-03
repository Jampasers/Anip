# cmd_refresh.py
from discord.ext import commands
import os
import re
import time
import tempfile
import asyncio
import requests
import discord

# ============ CONFIG ============
API_URL = os.getenv("REFRESH_API_URL", "http://23.137.105.146:5050/generate_token")
DEFAULT_PROXY = os.getenv(
    "DEFAULT_PROXY",
    "growtechcentral.com:10000:f44c5d7bf63ce6d4d4ab:c98f897ffef305b0"
)
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "60"))
PROGRESS_UPDATE_EVERY = 1  # update progress setiap berapa akun
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
# =================================

def mask_email(email: str) -> str:
    """
    Mask email supaya local part ketutup tapi domain tetap kelihatan.
    Contoh: hanif@example.com -> h****@example.com
    """
    try:
        local, domain = email.split("@", 1)
    except Exception:
        return "****@****"
    if not local:
        return "****@" + domain
    # tampilkan 1 huruf pertama, sisanya *
    return local[0] + ("*" * max(0, len(local) - 1)) + "@" + domain

async def do_post(api_url: str, payload: dict, headers: dict, timeout: int):
    """
    Jalankan requests.post di thread terpisah supaya tidak block event loop.
    Return (resp, False) jika sukses, atau (exception, True) jika error.
    """
    def _sync_post():
        return requests.post(api_url, headers=headers, json=payload, timeout=timeout)

    try:
        resp = await asyncio.to_thread(_sync_post)
        return resp, False
    except Exception as e:
        return e, True

def _parse_possible_token(j: dict):
    """
    Ambil token dari response JSON dengan fallback yang 'waras'.
    - Prioritas: j["token"] jika string & tidak kosong
    - Fallback: j["data"] / j["result"] jika string & mengandung '|'
    """
    tok = j.get("token")
    if isinstance(tok, str) and tok.strip():
        return tok.strip(), "direct"
    # fallback 'guessed'
    for k in ("token", "data", "result"):
        v = j.get(k)
        if isinstance(v, str) and "|" in v:
            return v.strip(), "guessed"
    return None, None

def setup(bot, c, conn, fmt_wl, PREFIX):
    @bot.command(name="refresh")
    async def refresh(ctx: commands.Context, gmails: str):
        """
        Refresh akun Gmail (cek DB dulu).
        Format: !refresh email1,email2,email3
        - Jika email tidak ada di tabel accounts -> NotFound.
        - Jika ada -> request ke API dan ambil token.
        """
        # parsing input → list email valid
        raw_list = [e.strip() for e in gmails.replace("\n", ",").split(",") if e.strip()]
        emails = [e for e in raw_list if EMAIL_RE.match(e)]
        if not emails:
            return await ctx.send("❌ Tidak ditemukan Gmail valid di input.")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (DiscordBot)"
        }

        # Counters dan hasil
        results = []             # baris output ke file
        success_count = 0
        fail_count = 0
        notfound_count = 0

        # Progress awal
        total = len(emails)
        masked_first = mask_email(emails[0]) if emails else "—"
        start_time = time.time()

        progress_msg = await ctx.send(
            f"⏳ Memulai proses refresh `{total}` akun...\n"
            f"Sedang diproses: **{masked_first}**\n"
            f"Sukses: 0 | Gagal: 0 | NotFound: 0\n"
            f"Waktu berjalan: 0 detik"
        )

        # Loop utama
        for idx, email in enumerate(emails, start=1):
            masked = mask_email(email)

            # 1) Cek database 'accounts'
            c.execute("CREATE TABLE IF NOT EXISTS accounts (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            c.execute("SELECT 1 FROM accounts WHERE email = ?", (email,))
            exists = c.fetchone() is not None

            if not exists:
                # Tidak ada di DB -> NotFound (tidak panggil API)
                results.append(f"{email} | NOT_FOUND")
                notfound_count += 1
                status_line = f"❌ Account not found: {masked}"
            else:
                # 2) Panggil API
                payload = {"email": email, "proxy": DEFAULT_PROXY}
                try:
                    resp_or_exc, is_exc = await do_post(API_URL, payload, headers, TIMEOUT)
                except Exception as e:
                    resp_or_exc = e
                    is_exc = True

                if is_exc:
                    # Exception pada request
                    results.append(email)
                    fail_count += 1
                    status_line = f"❌ Request error untuk {masked}"
                else:
                    resp = resp_or_exc
                    if getattr(resp, "status_code", 0) != 200:
                        # Status HTTP bukan 200
                        results.append(email)
                        fail_count += 1
                        status_line = f"❌ HTTP {resp.status_code} untuk {masked}"
                    else:
                        # Coba parse JSON
                        try:
                            j = resp.json()
                        except Exception:
                            results.append(email)
                            fail_count += 1
                            status_line = f"❌ JSON tidak valid untuk {masked}"
                        else:
                            token_value, mode = _parse_possible_token(j)
                            if token_value:
                                results.append(token_value)
                                success_count += 1
                                if mode == "guessed":
                                    status_line = f"✅ Berhasil (guessed): {masked}"
                                else:
                                    status_line = f"✅ Berhasil: {masked}"
                            else:
                                results.append(email)
                                fail_count += 1
                                status_line = f"❌ Gagal API untuk {masked}"

            # 3) Update progress berkala
            if idx % PROGRESS_UPDATE_EVERY == 0 or idx == total:
                elapsed = int(time.time() - start_time)
                try:
                    await progress_msg.edit(content=(
                        f"⏳ Memproses akun {idx}/{total}\n"
                        f"Terakhir diproses: **{masked}**\n\n"
                        f"{status_line}\n\n"
                        f"Sukses: **{success_count}** | Gagal: **{fail_count}** | NotFound: **{notfound_count}**\n"
                        f"Waktu berjalan: {elapsed} detik"
                    ))
                except Exception:
                    # ignore edit errors (misal message dihapus)
                    pass

        # 4) Tulis hasil ke file temp
        tmp_path = None
        try:
            tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8",
                                              prefix="refreshed_tokens_", suffix=".txt")
            tmp_path = tmp.name
            tmp.write("\n".join(results))
            tmp.close()
        except Exception as e:
            return await ctx.send(f"❌ Gagal membuat file hasil: `{e}`")

        # 5) Summary + DM ke author (fallback ke channel)
        summary_text = (
            f"✅ Selesai. Total diproses: {total} — "
            f"Sukses: {success_count}, Gagal: {fail_count}, NotFound: {notfound_count}"
        )

        dm_sent = False
        try:
            await ctx.author.send(
                content=summary_text,
                file=discord.File(tmp_path, filename="refreshed_tokens.txt")
            )
            dm_sent = True
        except Exception:
            dm_sent = False

        try:
            if dm_sent:
                await ctx.send("✅ Hasil sudah dikirim via DM ke kamu.")
            else:
                await ctx.send(
                    content=f"{summary_text}\n⚠️ Gagal kirim DM — hasil dikirim di sini.",
                    file=discord.File(tmp_path, filename="refreshed_tokens.txt")
                )
        finally:
            # 6) Cleanup file temp
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
