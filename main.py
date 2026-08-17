#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Sans Carki ve Cip Satis Botu
python-telegram-bot v20+ ile yazilmistir.
Tek dosyalik, uretime hazir Telegram botu.

Calistirmak icin:
1) pip install python-telegram-bot==21.6
2) BOT_TOKEN ortam degiskenini ayarlayin (Replit -> Secrets -> BOT_TOKEN)
3) python main.py
"""

import os
import sqlite3
import logging
import asyncio
from datetime import datetime

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    PreCheckoutQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ------------------------------------------------------------------
# AYARLAR
# ------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
# DB_PATH: Railway'de kalici disk (Volume) baglandiysa DB_PATH degiskenini
# o disk yoluna ayarlayin (orn: /data/casino_bot.db). Ayarlanmazsa, veritabani
# konteyner ile birlikte silinebilir (kalici olmaz).
DB_PATH = os.getenv("DB_PATH", "casino_bot.db")

CHIP_PACK_AMOUNT = 10      # 100 Stars karsiligi verilecek cip miktari
CHIP_PACK_PRICE = 100      # Telegram Stars (XTR) tutari
FREE_START_CHIPS = 1       # /start ile verilecek ucretsiz cip
DAILY_BONUS_CHIPS = 1      # gunluk bonus ile verilecek cip
DAILY_BONUS_HOURS = 24     # gunluk bonus icin bekleme suresi (saat)

# --------------------------------------------------------------
# SEVIYE (ROZET) SISTEMI
# --------------------------------------------------------------
# Esik (toplam kazanilan cip) -> (emoji, isim)
LEVELS = [
    (0, "🥚", "Acemi"),
    (10, "🎯", "Amator"),
    (50, "🔥", "Usta"),
    (150, "👑", "Efsane"),
    (500, "💎", "Kral"),
]


def get_level(total_won: int):
    current = LEVELS[0]
    for threshold, emoji, name in LEVELS:
        if total_won >= threshold:
            current = (threshold, emoji, name)
    return current[1], current[2]

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# VERITABANI YARDIMCI FONKSIYONLARI
# ------------------------------------------------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            chips INTEGER NOT NULL DEFAULT 0,
            last_spin TEXT
        )
        """
    )
    conn.commit()

    # Eski veritabanlarinda olmayabilecek kolonlari ekle (gecis / migration).
    # Kolon zaten varsa hata verir, bu hatayi guvenle yok sayariz.
    new_columns = [
        ("total_won", "INTEGER NOT NULL DEFAULT 0"),
        ("games_played", "INTEGER NOT NULL DEFAULT 0"),
        ("last_bonus", "TEXT"),
        ("display_name", "TEXT NOT NULL DEFAULT ''"),
    ]
    for col_name, col_def in new_columns:
        try:
            cur.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}")
            conn.commit()
        except sqlite3.OperationalError:
            pass  # kolon zaten var

    conn.close()


def get_or_create_user(user_id: int, display_name: str = None) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT user_id, chips, last_spin, total_won, games_played, last_bonus, display_name "
        "FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users (user_id, chips, last_spin, total_won, games_played, last_bonus, display_name) "
            "VALUES (?, ?, ?, 0, 0, NULL, ?)",
            (user_id, FREE_START_CHIPS, None, display_name or ""),
        )
        conn.commit()
        result = {
            "user_id": user_id,
            "chips": FREE_START_CHIPS,
            "last_spin": None,
            "total_won": 0,
            "games_played": 0,
            "last_bonus": None,
            "display_name": display_name or "",
        }
    else:
        result = {
            "user_id": row[0],
            "chips": row[1],
            "last_spin": row[2],
            "total_won": row[3],
            "games_played": row[4],
            "last_bonus": row[5],
            "display_name": row[6],
        }
        if display_name and display_name != row[6]:
            cur.execute(
                "UPDATE users SET display_name = ? WHERE user_id = ?",
                (display_name, user_id),
            )
            conn.commit()
            result["display_name"] = display_name
    conn.close()
    return result


def get_chips(user_id: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT chips FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def add_chips(user_id: int, amount: int) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET chips = chips + ? WHERE user_id = ?", (amount, user_id))
    conn.commit()
    cur.execute("SELECT chips FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row[0] if row else 0


def set_last_spin(user_id: int, ts: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("UPDATE users SET last_spin = ? WHERE user_id = ?", (ts, user_id))
    conn.commit()
    conn.close()


def record_spin_result(user_id: int, won: int) -> dict:
    """Bir spin sonucunu isler: cip ekler, toplam kazanc ve oynanan oyun sayisini gunceller."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "UPDATE users SET chips = chips + ?, total_won = total_won + ?, "
        "games_played = games_played + 1 WHERE user_id = ?",
        (won, won, user_id),
    )
    conn.commit()
    cur.execute(
        "SELECT chips, total_won, games_played FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return {"chips": row[0], "total_won": row[1], "games_played": row[2]}


def get_user_stats(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT chips, total_won, games_played, last_bonus FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    if row is None:
        return {"chips": 0, "total_won": 0, "games_played": 0, "last_bonus": None}
    return {"chips": row[0], "total_won": row[1], "games_played": row[2], "last_bonus": row[3]}


def claim_daily_bonus(user_id: int):
    """
    Gunluk bonusu vermeye calisir. Basariliysa (True, yeni_bakiye) doner.
    Basarisizsa (False, kalan_saat) doner.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT last_bonus FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    last_bonus_str = row[0] if row else None

    now = datetime.utcnow()
    if last_bonus_str:
        last_bonus = datetime.fromisoformat(last_bonus_str)
        elapsed_hours = (now - last_bonus).total_seconds() / 3600
        if elapsed_hours < DAILY_BONUS_HOURS:
            remaining = DAILY_BONUS_HOURS - elapsed_hours
            conn.close()
            return False, remaining

    cur.execute(
        "UPDATE users SET chips = chips + ?, last_bonus = ? WHERE user_id = ?",
        (DAILY_BONUS_CHIPS, now.isoformat(), user_id),
    )
    conn.commit()
    cur.execute("SELECT chips FROM users WHERE user_id = ?", (user_id,))
    new_balance = cur.fetchone()[0]
    conn.close()
    return True, new_balance


def get_leaderboard(limit: int = 10):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT display_name, total_won, user_id FROM users "
        "WHERE total_won > 0 ORDER BY total_won DESC LIMIT ?",
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()
    return rows


# ------------------------------------------------------------------
# ARAYUZ (KEYBOARD) YARDIMCILARI
# ------------------------------------------------------------------
def main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("🎡 Carki Cevir", callback_data="spin")],
        [InlineKeyboardButton(
            f"💰 Cip Satin Al ({CHIP_PACK_PRICE} ⭐️ = {CHIP_PACK_AMOUNT} Cip)",
            callback_data="buy",
        )],
        [InlineKeyboardButton("🎁 Gunluk Bonus", callback_data="daily_bonus")],
        [InlineKeyboardButton("🏆 Liderlik Tablosu", callback_data="leaderboard")],
        [InlineKeyboardButton("👤 Profilim / Rozetim", callback_data="profile")],
    ]
    return InlineKeyboardMarkup(keyboard)


def back_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⬅️ Ana Menu", callback_data="back_menu")]]
    )


def buy_or_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(
                f"💰 Cip Satin Al ({CHIP_PACK_PRICE} ⭐️ = {CHIP_PACK_AMOUNT} Cip)",
                callback_data="buy",
            )],
            [InlineKeyboardButton("⬅️ Ana Menu", callback_data="back_menu")],
        ]
    )


# ------------------------------------------------------------------
# KOMUT / MESAJ HANDLER'LARI
# ------------------------------------------------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    display_name = user.first_name or user.username or "Oyuncu"
    data = get_or_create_user(user.id, display_name=display_name)
    text = (
        f"👋 Merhaba {user.first_name}!\n\n"
        f"🎰 *Sans Carki Botu*'na hos geldin!\n"
        f"Su anki bakiyen: *{data['chips']} Cip* 🪙\n\n"
        "Asagidaki menuden islem secebilirsin:"
    )
    await update.message.reply_text(
        text, parse_mode="Markdown", reply_markup=main_menu_keyboard()
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    display_name = user.first_name or user.username or "Oyuncu"
    get_or_create_user(user.id, display_name=display_name)

    if query.data == "spin":
        await handle_spin(query, context)
    elif query.data == "buy":
        await send_chip_invoice(update, context)
    elif query.data == "profile":
        await handle_profile(query, context)
    elif query.data == "daily_bonus":
        await handle_daily_bonus(query, context)
    elif query.data == "leaderboard":
        await handle_leaderboard(query, context)
    elif query.data == "back_menu":
        await query.edit_message_text("🏠 Ana Menu", reply_markup=main_menu_keyboard())


async def handle_profile(query, context: ContextTypes.DEFAULT_TYPE):
    stats = get_user_stats(query.from_user.id)
    emoji, level_name = get_level(stats["total_won"])
    text = (
        f"👤 *Profilin*\n\n"
        f"🪙 Cip Bakiyen: *{stats['chips']}*\n"
        f"{emoji} Seviyen: *{level_name}*\n"
        f"🎯 Toplam Kazanilan Cip: *{stats['total_won']}*\n"
        f"🎮 Toplam Cevirme: *{stats['games_played']}*"
    )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=back_menu_keyboard()
    )


async def handle_daily_bonus(query, context: ContextTypes.DEFAULT_TYPE):
    success, value = claim_daily_bonus(query.from_user.id)
    if success:
        text = (
            f"🎁 Gunluk bonusunu aldin!\n\n"
            f"*+{DAILY_BONUS_CHIPS} Cip* hesabina eklendi.\n"
            f"🪙 Guncel Bakiye: *{value} Cip*\n\n"
            "Yarin tekrar ugra!"
        )
    else:
        hours = int(value)
        minutes = int((value - hours) * 60)
        text = (
            f"⏳ Gunluk bonusunu zaten aldin!\n\n"
            f"Bir sonraki bonus icin yaklasik *{hours} saat {minutes} dakika* bekle."
        )
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=back_menu_keyboard()
    )


async def handle_leaderboard(query, context: ContextTypes.DEFAULT_TYPE):
    rows = get_leaderboard(limit=10)
    if not rows:
        text = "🏆 *Liderlik Tablosu*\n\nHenuz kimse cip kazanmadi. Ilk sen ol!"
    else:
        medals = ["🥇", "🥈", "🥉"]
        lines = ["🏆 *Liderlik Tablosu* (Toplam Kazanilan Cip)\n"]
        for i, (display_name, total_won, user_id) in enumerate(rows):
            rank_icon = medals[i] if i < 3 else f"{i + 1}."
            name = display_name or "Oyuncu"
            lines.append(f"{rank_icon} {name} — *{total_won}* cip")
        text = "\n".join(lines)
    await query.edit_message_text(
        text, parse_mode="Markdown", reply_markup=back_menu_keyboard()
    )


# ------------------------------------------------------------------
# ODEME (TELEGRAM STARS / XTR)
# ------------------------------------------------------------------
async def send_chip_invoice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    title = "10 Adet Oyun Cipi"
    description = "Sans carkinda kullanabileceginiz 10 cip."
    payload = "10_chips_pack"
    currency = "XTR"
    prices = [LabeledPrice(label="10 Cip", amount=CHIP_PACK_PRICE)]

    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",  # Telegram Stars (XTR) odemelerinde bos birakilir
        currency=currency,
        prices=prices,
    )


async def precheckout_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.pre_checkout_query
    if query.invoice_payload == "10_chips_pack":
        await query.answer(ok=True)
    else:
        await query.answer(ok=False, error_message="Gecersiz siparis, lutfen tekrar deneyin.")


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    payment = update.message.successful_payment
    if payment.invoice_payload == "10_chips_pack":
        new_balance = add_chips(user_id, CHIP_PACK_AMOUNT)
        await update.message.reply_text(
            f"✅ Odeme basarili! Hesabina *{CHIP_PACK_AMOUNT} Cip* eklendi.\n"
            f"🪙 Guncel Bakiye: *{new_balance} Cip*",
            parse_mode="Markdown",
            reply_markup=main_menu_keyboard(),
        )


# ------------------------------------------------------------------
# SANS CARKI (SLOT) MANTIGI
# ------------------------------------------------------------------
def calculate_slot_prize(dice_value: int):
    """
    Odul, Telegram'in gonderdigi GERCEK slot sonucuna (dice_value, 1-64) gore
    hesaplanir. Ekranda gorunen ile kazanc HER ZAMAN birebir uyusur.
    """
    v = dice_value - 1
    reel1 = v // 16
    reel2 = (v % 16) // 4
    reel3 = v % 4
    symbols = ["🅱️ BAR", "🍇 Uzum", "🍋 Limon", "7️⃣ Yedi"]

    if reel1 == reel2 == reel3:
        if reel1 == 3:
            return 30, "🎉 JACKPOT! 7️⃣-7️⃣-7️⃣! *+30 Cip* kazandiniz!"
        return 6, f"🎊 Uclu eslesme ({symbols[reel1]})! *+6 Cip* kazandiniz!"
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        return 0, "🤏 Yakin kacis! Iki sembol eslesti ama odul yok. Tekrar dene!"
    else:
        return 0, "😔 Bu sefer kazanamadiniz. Tekrar deneyin!"


async def handle_spin(query, context: ContextTypes.DEFAULT_TYPE):
    user_id = query.from_user.id
    chat_id = query.message.chat_id
    chips = get_chips(user_id)

    if chips < 1:
        await query.edit_message_text(
            "❌ Yeterli cipin yok!\nCark cevirmek icin en az 1 cipe ihtiyacin var.\n\n"
            "Asagidan cip satin alabilirsin:",
            reply_markup=buy_or_back_keyboard(),
        )
        return

    # 1 cip dus
    add_chips(user_id, -1)
    set_last_spin(user_id, datetime.utcnow().isoformat())

    await query.edit_message_text("🎰 Cark cevriliyor...")

    dice_message = await context.bot.send_dice(chat_id=chat_id, emoji="🎰")
    dice_value = dice_message.dice.value

    # Telegram'in slot animasyonu yaklasik 4 saniye surer.
    # Sonucu, animasyon bitmeden ACIKLAMIYORUZ ki gorsel ile mesaj
    # her zaman birebir uyumlu olsun (onceki "bug gibi" gorunen sorun buydu).
    await asyncio.sleep(4)

    won, result_text = calculate_slot_prize(dice_value)
    stats = record_spin_result(user_id, won)

    final_text = f"{result_text}\n\n🪙 Guncel Bakiye: *{stats['chips']} Cip*"
    await context.bot.send_message(
        chat_id=chat_id,
        text=final_text,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


# ------------------------------------------------------------------
# HATA YONETIMI
# ------------------------------------------------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Hata olustu: %s", context.error, exc_info=context.error)


# ------------------------------------------------------------------
# ANA FONKSIYON
# ------------------------------------------------------------------
def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "BOT_TOKEN ortam degiskeni bulunamadi! "
            "Lutfen BOT_TOKEN ortam degiskenini ayarlayin."
        )

    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(PreCheckoutQueryHandler(precheckout_callback))
    application.add_handler(
        MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback)
    )
    application.add_error_handler(error_handler)

    logger.info("Bot baslatiliyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
