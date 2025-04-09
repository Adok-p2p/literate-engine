import os
import logging
from datetime import datetime
from functools import lru_cache
import requests
import pytz
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackQueryHandler
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from keep_alive import keep_alive

# --- НАСТРОЙКА ---
BOT_TOKEN = os.getenv('TELEGRAM_TOKEN')
kz_timezone = pytz.timezone('Asia/Almaty')
alert_price = {}  # user_id -> цена

# --- ЛОГИ ---
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- ЗАПРОС К BYBIT ---
@lru_cache(maxsize=1)
def get_bybit_p2p_offers(side="1", token="USDT", currency="KZT"):
    url = "https://api2.bybit.com/fiat/otc/item/online"
    payload = {
        "tokenId": token,
        "currencyId": currency,
        "side": side,
        "page": "1",
        "amount": "1000"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Ошибка P2P API: {e}")
        return None

# --- КОМАНДЫ ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💰 Курс USDT", callback_data='get_price')],
        [InlineKeyboardButton("📊 Спред P2P", callback_data='get_spread')],
        [InlineKeyboardButton("🔍 Лучшие предложения", callback_data='show_offers')]
    ]
    await update.message.reply_text(
        "🏦 *Бот для арбитража Bybit P2P*\nВыберите действие:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )

async def show_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_bybit_p2p_offers(side="1")
    if data and data.get("result"):
        offer = data["result"]["items"][0]
        await query.edit_message_text(
            text=(
                f"💰 *1 USDT = {offer['price']} KZT*\n"
                f"• Доступно: {offer['quantity']} USDT\n"
                f"• Продавец: {offer['nickName']} ({offer['completedOrderNum']} сделок)\n"
                f"⏱ {datetime.now(kz_timezone).strftime('%H:%M %d.%m.%Y')}"
            ),
            parse_mode="Markdown"
        )

async def show_spread(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    buy = get_bybit_p2p_offers(side="1")
    sell = get_bybit_p2p_offers(side="0")
    if buy and sell:
        spread = float(buy["result"]["items"][0]["price"]) - float(sell["result"]["items"][0]["price"])
        await query.edit_message_text(
            text=(
                f"📊 *Спред P2P*\n"
                f"• Покупка: {buy['result']['items'][0]['price']} KZT\n"
                f"• Продажа: {sell['result']['items'][0]['price']} KZT\n"
                f"🔸 Разница: {spread:.2f} KZT"
            ),
            parse_mode="Markdown"
        )

async def show_best_offers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_bybit_p2p_offers(side="1")
    if data and data.get("result"):
        offers = data["result"]["items"][:5]
        message = "🏆 *Топ-5 предложений для покупки USDT*\n\n"
        for i, offer in enumerate(offers, 1):
            rating = offer.get('completedRate', 'N/A')
            message += (
                f"{i}. {offer['price']} KZT\n"
                f"   • {offer['quantity']} USDT доступно\n"
                f"   • Рейтинг: {rating}%\n\n"
            )
        await query.edit_message_text(
            text=message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Назад", callback_data='back_to_main')]
            ])
        )

async def back_to_main(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await start(query, context)

# --- Уведомление ---
async def set_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(context.args[0])
        user_id = update.effective_user.id
        alert_price[user_id] = price
        await update.message.reply_text(f"✅ Уведомление установлено: сообщу, когда цена <= {price} KZT")
    except:
        await update.message.reply_text("⚠️ Используй команду так: /set_alert 535")

# --- Проверка цены ---
async def price_checker(app):
    data = get_bybit_p2p_offers(side="1")
    if not data or "result" not in data:
        return
    price = float(data["result"]["items"][0]["price"])
    for user_id, target_price in list(alert_price.items()):
        if price <= target_price:
            try:
                await app.bot.send_message(
                    chat_id=user_id,
                    text=f"📉 Цена упала до *{price} KZT*!",
                    parse_mode="Markdown"
                )
                del alert_price[user_id]
            except Exception as e:
                logger.error(f"Не удалось отправить сообщение {user_id}: {e}")

# --- ЗАПУСК ---
if __name__ == "__main__":
    keep_alive()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_alert", set_alert))
    app.add_handler(CallbackQueryHandler(show_price, pattern='^get_price$'))
    app.add_handler(CallbackQueryHandler(show_spread, pattern='^get_spread$'))
    app.add_handler(CallbackQueryHandler(show_best_offers, pattern='^show_offers$'))
    app.add_handler(CallbackQueryHandler(back_to_main, pattern='^back_to_main$'))

    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: price_checker(app), "interval", seconds=60)
    scheduler.start()

    logger.info("Бот запущен!")
    app.run_polling()