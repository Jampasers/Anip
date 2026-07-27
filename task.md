# Bot DC Status Update

## Activity Log
- **[2026-06-29]**: Upgraded QRIS system from static to dynamic.
    - Configured `QRIS_STATIC_STRING` in `.env` to store the merchant's base static QRIS payload.
    - Implemented dynamic QRIS generator (`make_dynamic_qris` and `parse_qris_tlv`) conforming to the EMVCo specification.
    - Automatically parses the base payload, sets initiation method to dynamic (`010212`), injects transaction amounts (Tag `54`), and recalculates CRC16-CCITT checksum (Tag `63`).
    - Integrated automatic dynamic QR image rendering in Discord DM billing flow using `qrcode` and Pillow with automated file cleanups.
    - **Fixed QRIS scanner error**:
        * Decoded the real static QR image (`QR/image.png`) using OpenCV to extract the true production QRIS payload (Hanif Store).
        * Discovered the initially provided string was a placeholder with typos.
        * Replaced `.env` and fallback config with the real production static QRIS string.
        * Removed the placeholder-specific length overrides (`tag == "26"` / `tag == "60"`) from `parse_qris_tlv`, allowing standard-compliant, native EMVCo parsing of the real payload without corruption.
- **[2026-06-26]**: Expanded Service System.
    - Added new services: Harvest + Move (823), Move Block/Seed (824), Splice Seed (825).
    - Implemented **Dynamic Pricing**: All service prices are now stored in the database.
    - Added `!hargajasa <code> <price>` to update service prices via command.
    - Removed **Buy PO** button and disabled automatic channel clearing for service persistence.
    - Moved service order channel ID setting from code to `.env`.
- **Status**: Running 🟢
- **Features Active**:
    - Global Slash Commands Synced
    - Service Menu Loop (30s interval) with Dynamic Pricing
    - Service Configuration (Code 821-825)
    - Auto Allocate Loop
    - Dynamic QRIS Generation & Deposit Monitor

## Notes for Team/Backend
- Virtual environment `venv-bot` issues remain; using system Python.
- `!service` now automatically resumes on startup if a channel is already configured.
- Bot is logged in as **Danstore#9326**.
