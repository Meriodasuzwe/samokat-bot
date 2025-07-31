import logging
import pprint
import os
import json
import sys

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

# ==== ЛОГИ ====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("railway.log")
    ]
)
logger = logging.getLogger(__name__)

# ==== ЗАГРУЗКА НАСТРОЕК ====
load_dotenv()
TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = [5092137530, 570326525]
CHANNEL_ID = "@SamokatControlAstana"

# ==== Google Sheets ====
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_json, scope)
gc = gspread.authorize(creds)
sheet = gc.open("Samokat Complaints").sheet1

# ==== Этапы FSM ====
MENU, OPERATOR, LOCATION, MEDIA, DESCRIPTION = range(5)

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

pending = {}

# ==== ОБРАБОТЧИКИ ====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return MENU
    await update.message.reply_text("Добро пожаловать!", reply_markup=main_menu)
    return MENU

async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text == "/start":
        return await start(update, context)

    text = update.message.text

    if text == "📤 Отправить жалобу":
        await update.message.reply_text("Выберите оператора:", reply_markup=operator_kb)
        return OPERATOR

    if text == "👤 Мой профиль":
        user = update.effective_user
        data = sheet.get_all_records()
        count = sum(1 for r in data if r["User"] == user.username or r["User"] == user.first_name)

        profile_text = (
            f"👤 Профиль\n"
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
    # Сохраняем файл и переходим к описанию
    media = update.message.photo[-1].file_id if update.message.photo else update.message.video.file_id
    context.user_data["media"] = media
    context.user_data["media_type"] = "photo" if update.message.photo else "video"

    await update.message.reply_text("📝 Опишите проблему (например: «Самокат мешает проходу»):")
    return DESCRIPTION

async def get_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text
    user = update.effective_user
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Сохраняем в таблицу без username
    row = [
        now,
        "аноним",  # вместо имени
        context.user_data["operator"],
        context.user_data["location"],
        context.user_data["media"],
        "ожидает",
        description
    ]
    sheet.append_row(row)

    msg_id = update.message.message_id
    pending[msg_id] = {
        "user_id": user.id,
        "operator": context.user_data["operator"],
        "location": context.user_data["location"],
        "media": context.user_data["media"],
        "media_type": context.user_data["media_type"],
        "description": description
    }

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Подтвердить", callback_data=f"confirm:{msg_id}")]
    ])
    text = (
        f"Новая жалоба\n"
        f"🛴 {context.user_data['operator']} // {context.user_data['location']}\n"
        f"Описание: {description}"
    )

    # Отправляем админам
    for admin in ADMIN_IDS:
        await context.bot.send_message(admin, text, reply_markup=kb)
        if context.user_data["media_type"] == "photo":
            await context.bot.send_photo(admin, context.user_data["media"])
        else:
            await context.bot.send_video(admin, context.user_data["media"])

    await update.message.reply_text(
        "✅ Жалоба отправлена. Ожидайте подтверждения.",
        reply_markup=after_report_kb
    )
    return MENU

async def confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id

    if user_id not in ADMIN_IDS:
        await query.answer("❌ У вас нет прав подтверждать жалобы.", show_alert=True)
        return

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

    caption = (
        f"🚨 Подтверждённая жалоба\n"
        f"🛴 {comp['operator']}\n📍 {comp['location']}\nОписание: {comp['description']}"
    )
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

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 Сброс состояния. Введите /start", reply_markup=main_menu)
    return ConversationHandler.END

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.error("❌ Произошла ошибка: %s", context.error)

# ==== ЗАПУСК ====
app = ApplicationBuilder().token(TOKEN).build()

conv = ConversationHandler(
    entry_points=[CommandHandler("start", start)],
    states={
        MENU: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_handler)],
        OPERATOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_operator)],
        LOCATION: [MessageHandler((filters.TEXT | filters.LOCATION) & ~filters.COMMAND, get_location)],
        MEDIA: [MessageHandler(filters.PHOTO | filters.VIDEO, get_media)],
        DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_description)],
    },
    fallbacks=[
        CommandHandler("cancel", cancel),
        CommandHandler("reset", reset)
    ]
)

app.add_handler(conv)
app.add_handler(CallbackQueryHandler(confirm_handler, pattern="^confirm:"))
app.add_error_handler(error_handler)

if __name__ == "__main__":
    print("🤖 Бот запущен.")
    app.run_polling(close_loop=False)
