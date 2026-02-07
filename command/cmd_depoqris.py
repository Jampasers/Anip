"""
QRIS Deposit Module - Pakasir Integration
Deposit WL menggunakan pembayaran QRIS via Pakasir
"""
import discord
from discord.ext import commands, tasks
import aiohttp
import asyncio
import os
import io
from datetime import datetime, timedelta
import qrcode
from dotenv import load_dotenv

load_dotenv()

# Pakasir API Config
PAKASIR_SLUG = os.getenv("PAKASIR_SLUG", "")
PAKASIR_API_KEY = os.getenv("PAKASIR_API_KEY", "")
PAKASIR_BASE_URL = "https://app.pakasir.com/api"

# Pricing: 1 WL = 2.1 Rupiah
WL_TO_RUPIAH = 2.1
MIN_DEPOSIT_RUPIAH = 500

# Globals
bot = None
c = None
conn = None
fmt_wl = None
PREFIX = "!"

def ensure_qris_deposits_schema(cur, connection):
    """Create qris_deposits table if not exists."""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS qris_deposits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id TEXT UNIQUE,
            user_id INTEGER,
            amount_rupiah INTEGER,
            amount_wl INTEGER,
            status TEXT DEFAULT 'pending',
            qr_string TEXT,
            expired_at TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP
        )
    """)
    connection.commit()

def convert_wl_to_rupiah(wl_amount: int) -> int:
    """Convert WL to Rupiah (1 WL = 2.1 Rupiah)."""
    return int(wl_amount * WL_TO_RUPIAH)

def convert_rupiah_to_wl(rupiah_amount: int) -> int:
    """Convert Rupiah to WL (1 WL = 2.1 Rupiah)."""
    return int(rupiah_amount / WL_TO_RUPIAH)

def parse_iso_datetime(iso_string: str) -> datetime | None:
    """
    Parse ISO datetime string, handling nanoseconds from Pakasir API.
    Pakasir returns 9 decimal places but Python only supports 6.
    """
    if not iso_string:
        return None
    
    try:
        # Replace Z with +00:00 for timezone
        iso_string = iso_string.replace('Z', '+00:00')
        
        # Handle nanoseconds (9 digits) by truncating to microseconds (6 digits)
        # Format: 2026-02-07T11:48:36.608498557+00:00
        import re
        # Match the decimal portion and truncate to 6 digits
        pattern = r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\.(\d+)([+-]\d{2}:\d{2})'
        match = re.match(pattern, iso_string)
        if match:
            base = match.group(1)
            decimals = match.group(2)[:6]  # Truncate to 6 digits (microseconds)
            tz = match.group(3)
            iso_string = f"{base}.{decimals}{tz}"
        
        return datetime.fromisoformat(iso_string)
    except Exception as e:
        print(f"[QRIS] Failed to parse datetime '{iso_string}': {e}")
        return None

def generate_order_id() -> str:
    """Generate unique order ID."""
    now = datetime.now()
    return f"DEP{now.strftime('%y%m%d%H%M%S')}{now.microsecond // 1000:03d}"

def generate_qr_image(qr_string: str) -> io.BytesIO:
    """Generate QR code image from QR string."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_string)
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img_buffer = io.BytesIO()
    img.save(img_buffer, format='PNG')
    img_buffer.seek(0)
    return img_buffer

async def create_qris_transaction(order_id: str, amount: int) -> dict | None:
    """
    Create QRIS transaction via Pakasir API.
    Returns payment data or None if failed.
    """
    if not PAKASIR_SLUG or not PAKASIR_API_KEY:
        print("[QRIS] Error: PAKASIR_SLUG or PAKASIR_API_KEY not configured")
        return None
    
    url = f"{PAKASIR_BASE_URL}/transactioncreate/qris"
    payload = {
        "project": PAKASIR_SLUG,
        "order_id": order_id,
        "amount": amount,
        "api_key": PAKASIR_API_KEY
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("payment")
                else:
                    text = await resp.text()
                    print(f"[QRIS] API Error: {resp.status} - {text}")
                    return None
    except Exception as e:
        print(f"[QRIS] Request Error: {e}")
        return None

async def check_transaction_status(order_id: str, amount: int) -> dict | None:
    """
    Check transaction status via Pakasir API.
    Returns transaction data or None if failed.
    """
    if not PAKASIR_SLUG or not PAKASIR_API_KEY:
        return None
    
    url = f"{PAKASIR_BASE_URL}/transactiondetail"
    params = {
        "project": PAKASIR_SLUG,
        "order_id": order_id,
        "amount": amount,
        "api_key": PAKASIR_API_KEY
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("transaction")
                return None
    except Exception as e:
        print(f"[QRIS] Check Status Error: {e}")
        return None

async def cancel_transaction(order_id: str, amount: int) -> bool:
    """Cancel transaction via Pakasir API."""
    if not PAKASIR_SLUG or not PAKASIR_API_KEY:
        return False
    
    url = f"{PAKASIR_BASE_URL}/transactioncancel"
    payload = {
        "project": PAKASIR_SLUG,
        "order_id": order_id,
        "amount": amount,
        "api_key": PAKASIR_API_KEY
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                return resp.status == 200
    except Exception as e:
        print(f"[QRIS] Cancel Error: {e}")
        return False

# ============================================================
# Background Task: Monitor Pending Deposits
# ============================================================
@tasks.loop(seconds=10)
async def monitor_pending_deposits():
    """Check status of pending deposits every 10 seconds."""
    if not c or not conn:
        return
    
    try:
        # Get all pending deposits
        c.execute("""
            SELECT id, order_id, user_id, amount_rupiah, amount_wl, expired_at 
            FROM qris_deposits 
            WHERE status = 'pending'
        """)
        pending = c.fetchall()
        
        for deposit in pending:
            dep_id, order_id, user_id, amount_rupiah, amount_wl, expired_at = deposit
            
            # Check if expired
            if expired_at:
                exp_time = parse_iso_datetime(expired_at)
                if exp_time and datetime.now(exp_time.tzinfo) > exp_time:
                    c.execute("UPDATE qris_deposits SET status = 'expired' WHERE id = ?", (dep_id,))
                    conn.commit()
                    print(f"[QRIS] Deposit {order_id} expired")
                    
                    # Notify user
                    try:
                        user = await bot.fetch_user(user_id)
                        await user.send(f"⏰ Deposit QRIS `{order_id}` telah expired. Silakan buat deposit baru.")
                    except:
                        pass
                    continue
            
            # Check payment status
            status_data = await check_transaction_status(order_id, amount_rupiah)
            if status_data and status_data.get("status") == "completed":
                # Payment successful!
                c.execute("""
                    UPDATE qris_deposits 
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP 
                    WHERE id = ?
                """, (dep_id,))
                
                # Add balance to user
                c.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
                row = c.fetchone()
                if row:
                    new_balance = (row[0] or 0) + amount_wl
                    c.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
                    conn.commit()
                    
                    print(f"[QRIS] Deposit {order_id} completed! User {user_id} +{amount_wl} WL")
                    
                    # Notify user
                    try:
                        user = await bot.fetch_user(user_id)
                        embed = discord.Embed(
                            title="✅ Deposit QRIS Berhasil!",
                            color=discord.Color.green()
                        )
                        embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
                        embed.add_field(name="Jumlah", value=f"Rp {amount_rupiah:,}".replace(",", "."), inline=True)
                        embed.add_field(name="WL Diterima", value=f"+{fmt_wl(amount_wl)} WL", inline=True)
                        embed.add_field(name="Saldo Sekarang", value=f"{fmt_wl(new_balance)} WL", inline=False)
                        await user.send(embed=embed)
                    except Exception as e:
                        print(f"[QRIS] Failed to notify user {user_id}: {e}")
                else:
                    conn.commit()
                    
    except Exception as e:
        print(f"[QRIS] Monitor Error: {e}")

# ============================================================
# Process Deposit Request
# ============================================================
async def process_qris_deposit(interaction: discord.Interaction, wl_amount: int) -> bool:
    """
    Process QRIS deposit request.
    Returns True if successful, False otherwise.
    """
    user = interaction.user
    user_id = user.id
    
    # Calculate rupiah amount
    rupiah_amount = convert_wl_to_rupiah(wl_amount)
    
    # Validate minimum deposit
    if rupiah_amount < MIN_DEPOSIT_RUPIAH:
        min_wl = int(MIN_DEPOSIT_RUPIAH / WL_TO_RUPIAH) + 1
        await interaction.followup.send(
            f"❌ Minimal deposit Rp {MIN_DEPOSIT_RUPIAH}. Kamu perlu deposit minimal {min_wl} WL.",
            ephemeral=True
        )
        return False
    
    # Check if user registered
    c.execute("SELECT nama FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send(
            "❌ Kamu belum register. Klik **SET GROWID** dulu.",
            ephemeral=True
        )
        return False
    
    growid = row[0]
    
    # Generate order ID
    order_id = generate_order_id()
    
    # Create QRIS transaction via Pakasir
    payment_data = await create_qris_transaction(order_id, rupiah_amount)
    
    if not payment_data:
        await interaction.followup.send(
            "❌ Gagal membuat transaksi QRIS. Coba lagi nanti.",
            ephemeral=True
        )
        return False
    
    qr_string = payment_data.get("payment_number", "")
    expired_at = payment_data.get("expired_at", "")
    total_payment = payment_data.get("total_payment", rupiah_amount)
    fee = payment_data.get("fee", 0)
    
    # Try to send DM first
    try:
        # Generate QR image
        qr_image = generate_qr_image(qr_string)
        
        embed = discord.Embed(
            title="💳 Invoice Deposit QRIS",
            color=discord.Color.blue()
        )
        embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
        embed.add_field(name="GrowID", value=growid, inline=True)
        embed.add_field(name="Jumlah WL", value=f"{fmt_wl(wl_amount)} WL", inline=True)
        embed.add_field(name="Amount", value=f"Rp {rupiah_amount:,}".replace(",", "."), inline=True)
        if fee > 0:
            embed.add_field(name="Fee", value=f"Rp {fee:,}".replace(",", "."), inline=True)
        embed.add_field(name="Total Bayar", value=f"**Rp {total_payment:,}**".replace(",", "."), inline=False)
        embed.add_field(name="⏰ Expired", value=expired_at[:19].replace("T", " ") if expired_at else "-", inline=False)
        embed.set_image(url="attachment://qris.png")
        embed.set_footer(text="Scan QR code dengan aplikasi e-wallet/mobile banking")
        
        await user.send(embed=embed, file=discord.File(qr_image, filename="qris.png"))
        
    except discord.Forbidden:
        # DM disabled - cancel deposit
        await cancel_transaction(order_id, rupiah_amount)
        await interaction.followup.send(
            "❌ DM kamu tidak aktif. Deposit dibatalkan.\n"
            "Aktifkan DM dari server ini lalu coba lagi.",
            ephemeral=True
        )
        return False
    except Exception as e:
        print(f"[QRIS] DM Error: {e}")
        await cancel_transaction(order_id, rupiah_amount)
        await interaction.followup.send(
            "❌ Gagal mengirim DM. Deposit dibatalkan.",
            ephemeral=True
        )
        return False
    
    # Save to database
    c.execute("""
        INSERT INTO qris_deposits (order_id, user_id, amount_rupiah, amount_wl, status, qr_string, expired_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
    """, (order_id, user_id, rupiah_amount, wl_amount, qr_string, expired_at))
    conn.commit()
    
    await interaction.followup.send(
        "✅ Invoice QRIS sudah dikirim ke DM kamu!\n"
        "Silakan scan QR code dan bayar sesuai nominal.",
        ephemeral=True
    )
    
    return True

# ============================================================
# Process Deposit Request (Rupiah Input)
# ============================================================
async def process_qris_deposit_rupiah(interaction: discord.Interaction, rupiah_amount: int) -> bool:
    """
    Process QRIS deposit request with Rupiah input.
    Converts Rupiah to WL automatically.
    Returns True if successful, False otherwise.
    """
    user = interaction.user
    user_id = user.id
    
    # Validate minimum deposit
    if rupiah_amount < MIN_DEPOSIT_RUPIAH:
        await interaction.followup.send(
            f"❌ Minimal deposit Rp {MIN_DEPOSIT_RUPIAH}.",
            ephemeral=True
        )
        return False
    
    # Calculate WL amount
    wl_amount = convert_rupiah_to_wl(rupiah_amount)
    
    # Check if user registered
    c.execute("SELECT nama FROM users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    if not row:
        await interaction.followup.send(
            "❌ Kamu belum register. Klik **SET GROWID** dulu.",
            ephemeral=True
        )
        return False
    
    growid = row[0]
    
    # Generate order ID
    order_id = generate_order_id()
    
    # Create QRIS transaction via Pakasir
    payment_data = await create_qris_transaction(order_id, rupiah_amount)
    
    if not payment_data:
        await interaction.followup.send(
            "❌ Gagal membuat transaksi QRIS. Coba lagi nanti.",
            ephemeral=True
        )
        return False
    
    qr_string = payment_data.get("payment_number", "")
    expired_at = payment_data.get("expired_at", "")
    total_payment = payment_data.get("total_payment", rupiah_amount)
    fee = payment_data.get("fee", 0)
    
    # Try to send DM first
    try:
        # Generate QR image
        qr_image = generate_qr_image(qr_string)
        
        embed = discord.Embed(
            title="💳 Invoice Deposit QRIS",
            color=discord.Color.blue()
        )
        embed.add_field(name="Order ID", value=f"`{order_id}`", inline=False)
        embed.add_field(name="GrowID", value=growid, inline=True)
        embed.add_field(name="Amount", value=f"Rp {rupiah_amount:,}".replace(",", "."), inline=True)
        embed.add_field(name="WL yang Didapat", value=f"{fmt_wl(wl_amount)} WL", inline=True)
        if fee > 0:
            embed.add_field(name="Fee", value=f"Rp {fee:,}".replace(",", "."), inline=True)
        embed.add_field(name="Total Bayar", value=f"**Rp {total_payment:,}**".replace(",", "."), inline=False)
        embed.add_field(name="⏰ Expired", value=expired_at[:19].replace("T", " ") if expired_at else "-", inline=False)
        embed.set_image(url="attachment://qris.png")
        embed.set_footer(text="Scan QR code dengan aplikasi e-wallet/mobile banking")
        
        await user.send(embed=embed, file=discord.File(qr_image, filename="qris.png"))
        
    except discord.Forbidden:
        # DM disabled - cancel deposit
        await cancel_transaction(order_id, rupiah_amount)
        await interaction.followup.send(
            "❌ DM kamu tidak aktif. Deposit dibatalkan.\n"
            "Aktifkan DM dari server ini lalu coba lagi.",
            ephemeral=True
        )
        return False
    except Exception as e:
        print(f"[QRIS] DM Error: {e}")
        await cancel_transaction(order_id, rupiah_amount)
        await interaction.followup.send(
            "❌ Gagal mengirim DM. Deposit dibatalkan.",
            ephemeral=True
        )
        return False
    
    # Save to database
    c.execute("""
        INSERT INTO qris_deposits (order_id, user_id, amount_rupiah, amount_wl, status, qr_string, expired_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
    """, (order_id, user_id, rupiah_amount, wl_amount, qr_string, expired_at))
    conn.commit()
    
    await interaction.followup.send(
        f"✅ Invoice QRIS sudah dikirim ke DM kamu!\n"
        f"💰 Rp {rupiah_amount:,} → {fmt_wl(wl_amount)} WL".replace(",", "."),
        ephemeral=True
    )
    
    return True

# ============================================================
# Setup function
# ============================================================
def setup(_bot, _c, _conn, _fmt_wl, _PREFIX):
    global bot, c, conn, fmt_wl, PREFIX
    bot = _bot
    c = _c
    conn = _conn
    fmt_wl = _fmt_wl
    PREFIX = _PREFIX
    
    # Ensure schema
    ensure_qris_deposits_schema(c, conn)
    
    # Start monitor task when bot is ready
    @bot.listen('on_ready')
    async def start_qris_monitor():
        if not monitor_pending_deposits.is_running():
            monitor_pending_deposits.start()
            print("[QRIS] Deposit monitor started")
