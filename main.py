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
DB_PATH = "casino_bot.db"

CHIP_PACK_AMOUNT = 10      # 100 Stars karsiligi verilecek cip miktari
CHIP_PACK_PRICE = 100      # Telegram Stars (XTR) tutari
FREE_START_CHIPS = 1       # /start ile verilecek ucretsiz cip

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
    conn.close()


def get_or_create_user(user_id: int) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT user_id, chips, last_spin FROM users WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    if row is None:
        cur.execute(
            "INSERT INTO users (user_id, chips, last_spin) VALUES (?, ?, ?)",
            (user_id, FREE_START_CHIPS, None),
        )
        conn.commit()
        result = {"user_id": user_id, "chips": FREE_START_CHIPS, "last_spin": None}
    else:
        result = {"user_id": row[0], "chips": row[1], "last_spin": row[2]}
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
        [InlineKeyboardButton("👤 Profilim / Cip Bakiye", callback_data="profile")],
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
    data = get_or_create_user(user.id)
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
    user_id = query.from_user.id
    get_or_create_user(user_id)

    if query.data == "spin":
        await handle_spin(query, context)
    elif query.data == "buy":
        await send_chip_invoice(update, context)
    elif query.data == "profile":
        await handle_profile(query, context)
    elif query.data == "back_menu":
        await query.edit_message_text("🏠 Ana Menu", reply_markup=main_menu_keyboard())


async def handle_profile(query, context: ContextTypes.DEFAULT_TYPE):
    chips = get_chips(query.from_user.id)
    text = f"👤 *Profilin*\n\n🪙 Cip Bakiyen: *{chips}*"
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
    Telegram 🎰 zar degeri 1-64 arasindadir.
    Bu deger 3 makaranin kombinasyonunu temsil eder.
    """
    v = dice_value - 1
    reel1 = v // 16
    reel2 = (v % 16) // 4
    reel3 = v % 4
    symbols = ["BAR", "🍇 Uzum", "🍋 Limon", "7️⃣ Yedi"]

    if reel1 == reel2 == reel3:
        if reel1 == 3:
            return 50, "🎉 JACKPOT! 7-7-7! *+50 Cip* kazandiniz!"
        return 15, f"🎊 Uclu eslesme ({symbols[reel1]})! *+15 Cip* kazandiniz!"
    elif reel1 == reel2 or reel2 == reel3 or reel1 == reel3:
        return 3, "✨ Ikili eslesme! *+3 Cip* kazandiniz!"
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

    # Telegram'in slot animasyonu yaklasik 4 saniye surer
    await asyncio.sleep(4)

    won, result_text = calculate_slot_prize(dice_value)
    if won > 0:
        new_balance = add_chips(user_id, won)
    else:
        new_balance = get_chips(user_id)

    final_text = f"{result_text}\n\n🪙 Guncel Bakiye: *{new_balance} Cip*"
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
