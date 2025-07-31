import logging
import pprint
import os
from dotenv import load_dotenv

from telegram import (
    Update, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler, CallbackQueryHandler
)
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime

# ==== ЗАГРУЗКА ТОКЕНА И НАСТРОЕК ====
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")  # токен из .env
ADMIN_ID = 5092137530           # твой Telegram ID
CHANNEL_ID = -1002864245674     # канал для публикации жалоб

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
gc = gspread.authorize(creds)
sheet = gc.open("Samokat Complaints").sheet1  # название таблицы

# ==== Этапы FSM ====
MENU, OPERATOR, LOCATION, MEDIA = range(4)
logging.basicConfig(level=logging.INFO)

# ==== Клавиатуры ====
main_menu = ReplyKeyboardMarkup(
    [["📤 Отправить жалобу", "👤 Мой профиль"], ["ℹ️ О проекте"]],
    resize_keyboard=True
)

operator_kb = ReplyKeyboardMarkup(
    [["Whoosh", "Yandex", "Jet", "Не знаю"], ["🔙 Назад"]],
    resize_keyboard=True
)

location_kb = ReplyKeyboardMarkup(
    [[KeyboardButton("📍 Отправить геолокацию", request_location=True)], ["🔙 Назад"]],
    resize_keyboard=True
)

after_report_kb = ReplyKeyboardMarkup(
    [["📤 Отправить ещё одну жалобу", "🏠 Главное меню"]],
    resize_keyboard=True
)

# Хранилище жалоб для подтверждения
pending = {}

# ==== ОБРАБОТЧИКИ ====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Добро пожаловать!", reply_markup=main_menu)
    return MENU

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📤 Отправить жалобу":
        await update.message.reply_text("Выберите оператора:", reply_markup=operator_kb)
        return OPERATOR

    if text == "👤 Мой профиль":
        user = update.effective_user
        data = sheet.get_all_records()

        # Считаем жалобы
        count = sum(1 for r in data if r["User"] == user.username or r["User"] == user.first_name)

        profile_text = (
            f"👤 Профиль @{user.username or user.first_name}\n"
            f"Жалоб отправлено: {count}\n"
            f"Вознаграждение: в процессе разработки"
        )

        await update.message.reply_text(profile_text, reply_markup=main_menu)
        return MENU

    if text == "ℹ️ О проекте":
        await update.message.reply_text(
            "Этот бот помогает фиксировать нарушения парковки электросамокатов в Астане.",
            reply_markup=main_menu
        )
        return MENU

    if text == "🏠 Главное меню":
        return await start(update, context)

    if text == "📤 Отправить ещё одну жалобу":
        await update.message.reply_text("Выберите оператора:", reply_markup=operator_kb)
        return OPERATOR

    return MENU

async def get_operator(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "🔙 Назад":
        return await start(update, context)
    context.user_data["operator"] = update.message.text
    await update.message.reply_text("📍 Укажите локацию:", reply_markup=location_kb)
    return LOCATION

async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    print("==== RAW UPDATE ====")
    pprint.pprint(update.to_dict())

    if update.message.text == "🔙 Назад":
        await update.message.reply_text("Выберите оператора:", reply_markup=operator_kb)
        return OPERATOR

    if update.message.location:
        loc = update.message.location
        context.user_data["location"] = f"{loc.latitude}, {loc.longitude}"
    else:
        context.user_data["location"] = update.message.text

    await update.message.reply_text("📸 Отправьте фото или видео нарушения:")
    return MEDIA

async def get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    media = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    username = user.username or user.first_name

    row = [now, username, context.user_data["operator"], context.user_data["location"], media, "ожидает"]
    sheet.append_row(row)

    msg_id = update.message.message_id
    pending[msg_id] = {
        "user_id": user.id,
        "username": username,
        "operator": context.user_data["operator"],
        "location": context.user_data["location"],
        "media": media,
        "media_type": "photo" if update.message.photo else "video"
    }

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm:{msg_id}")]
    ])
    text = f"Новая жалоба от @{username}\n🛴 {context.user_data['operator']} // {context.user_data['location']}"
    await context.bot.send_message(ADMIN_ID, text, reply_markup=kb)
    if update.message.photo:
        await context.bot.send_photo(ADMIN_ID, media)
    else:
        await context.bot.send_video(ADMIN_ID, media)

    await update.message.reply_text(
        "✅ Жалоба отправлена. Ожидайте подтверждения.",
        reply_markup=after_report_kb
    )
    return MENU

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not query.data.startswith("confirm:"):
        return
    msg_id = int(query.data.split(":")[1])
    comp = pending.pop(msg_id, None)
    if not comp:
        await query.edit_message_text("❗️ Жалоба уже подтверждена или не найдена.")
        return

    rows = sheet.get_all_values()
    for i, row in enumerate(rows, start=1):
        if row[4] == comp["media"]:
            sheet.update_cell(i, 6, "подтверждено")
            break

    caption = f"🚨 Подтверждённая жалоба\n🛴 {comp['operator']}\n📍 {comp['location']}\n👤 @{comp['username']}"
    if comp["media_type"] == "photo":
        await context.bot.send_photo(CHANNEL_ID, comp["media"], caption=caption)
    else:
        await context.bot.send_video(CHANNEL_ID, comp["media"], caption=caption)

    await context.bot.send_message(comp["user_id"], "✅ Ваша жалоба подтверждена и отправлена!")
    await query.edit_message_text("✅ Жалоба подтверждена и опубликована.")
    return MENU

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Действие отменено.", reply_markup=main_menu)
    return MENU

# ==== ЗАПУСК ====
app = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)],
        OPERATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_operator)],
        LOCATION: [MessageHandler((filters.TEXT | filters.LOCATION) & ~filters.COMMAND, get_location)],
        MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, get_media)],
    },
    fallbacks=[CommandHandler("cancel", cancel)]
)

app.add_handler(conv)
app.add_handler(CallbackQueryHandler(confirm_handler, pattern="^confirm:"))
print("🤖 Бот запущен.")
app.run_polling()
