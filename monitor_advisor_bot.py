"""
🖥️ Telegram-бот: Помощник по выбору монитора
На базе Groq API (бесплатный лимит) + модель LLaMA 3

Установка зависимостей:
    pip install python-telegram-bot groq

Получение токенов:
    TELEGRAM_TOKEN   — напишите @BotFather в Telegram → /newbot
    GROQ_API_KEY     — зарегистрируйтесь на https://console.groq.com (бесплатно)
"""

import asyncio
import logging
import re
import urllib.parse
from groq import Groq
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ─── Настройки ────────────────────────────────────────────────────────────────

import os
from dotenv import load_dotenv
load_dotenv()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")
ADMIN_ID       = int(os.environ.get("ADMIN_ID", 0))

GROQ_MODEL = "llama-3.3-70b-versatile"
MAX_HISTORY = 16

SYSTEM_PROMPT = """Ты — эксперт по выбору мониторов. Твоя задача — помогать пользователям 
выбрать идеальный монитор под их сферу деятельности и бюджет.

Ты хорошо разбираешься в:
- Характеристиках мониторов: разрешение, частота обновления, тип матрицы (IPS, VA, TN, OLED), 
  время отклика, яркость, цветовой охват (sRGB, DCI-P3, Adobe RGB), HDR, порты подключения.
- Подборе мониторов для разных сфер: геймеры, дизайнеры, фотографы, программисты, 
  офисные работники, видеомонтажёры, архитекторы, стримеры.
- Популярных брендах: LG, Samsung, ASUS, Dell, BenQ, AOC, Philips, ViewSonic, MSI.
- Соотношении цена/качество в разных ценовых сегментах.

Правила общения:
- Задавай уточняющие вопросы: сфера деятельности, бюджет, размер экрана, особые пожелания.
- Давай конкретные рекомендации с точными названиями моделей (например: LG 27GP850-B, ASUS VG279QM).
- Объясняй, ПОЧЕМУ та или иная характеристика важна для конкретной задачи.
- Общайся на языке пользователя (русский или другой).
- ВАЖНО: Всегда указывай точное название модели в формате "БРЕНД АРТИКУЛ", например: LG 27GP850-B, Samsung C27G75T.

СТРОГИЕ ПРАВИЛА — НИКОГДА НЕ НАРУШАЙ:
1. Если вопрос НЕ связан с мониторами или дисплеями — ОТКАЖИСЬ отвечать.
2. Не отвечай на вопросы о медицине, еде, играх, людях, новостях и любых других темах.
3. При отказе говори ТОЛЬКО: «Я специализируюсь исключительно на мониторах. Задайте вопрос про мониторы!»
4. Не объясняй почему отказываешь, не извиняйся, не давай никакой другой информации.
"""

# Быстрые кнопки сфер деятельности
QUICK_TOPICS = [
    ["🎮 Геймер", "🎨 Дизайнер/Фотограф"],
    ["💻 Программист", "🎬 Видеомонтаж"],
    ["🏢 Офис/Работа", "🏗️ Архитектура/CAD"],
]

# ─── Маркетплейсы ─────────────────────────────────────────────────────────────

MARKETPLACES = [
    ("Ozon",           "https://www.ozon.ru/search/?text={}"),
    ("Wildberries",    "https://www.wildberries.ru/catalog/0/search.aspx?search={}"),
    ("DNS",            "https://www.dns-shop.ru/search/?q={}"),
    ("Яндекс.Маркет", "https://market.yandex.ru/search?text={}"),
    ("Citilink",       "https://www.citilink.ru/search/?text={}"),
]

# Паттерн для поиска моделей мониторов (БРЕНД АРТИКУЛ)
# Примеры: LG 27GP850-B, ASUS VG279QM, Samsung C27G75T, Dell U2722D
MODEL_PATTERN = re.compile(
    r'\b(LG|ASUS|Acer|Samsung|Dell|BenQ|AOC|Philips|ViewSonic|MSI|HP|Lenovo|Gigabyte|HUAWEI)'
    r'\s+(?:Predator\s+|Odyssey\s+|ProArt\s+|TUF\s+|ROG\s+)?([A-Z0-9]{2,}(?:[-][A-Z0-9]+)*)\b'
)


def generate_links_block(model_name: str) -> str:
    """Генерирует блок ссылок для одной модели монитора."""
    encoded = urllib.parse.quote(model_name)
    lines = [f"🛒 *Купить {model_name}:*"]
    for name, url_template in MARKETPLACES:
        lines.append(f"• [{name}]({url_template.format(encoded)})")
    return "\n".join(lines)


def append_market_links(reply: str) -> str:
    """Находит модели мониторов в ответе и добавляет блок ссылок."""
    matches = MODEL_PATTERN.findall(reply)
    if not matches:
        return reply

    # Убираем дубли, берём первые 3 модели
    seen = set()
    unique_models = []
    for brand, model in matches:
        full_name = f"{brand} {model}"
        if full_name not in seen:
            seen.add(full_name)
            unique_models.append(full_name)
        if len(unique_models) >= 3:
            break

    links_section = "\n\n─────────────────────\n" + "\n\n".join(
        generate_links_block(m) for m in unique_models
    )
    return reply + links_section


# ─── Инициализация ────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

groq_client = Groq(api_key=GROQ_API_KEY)

# Хранилище истории: { user_id: [ {role, content}, ... ] }
user_histories: dict[int, list[dict]] = {}


# ─── Вспомогательные функции ──────────────────────────────────────────────────

def get_history(user_id: int) -> list[dict]:
    return user_histories.setdefault(user_id, [])


def add_message(user_id: int, role: str, content: str) -> None:
    history = get_history(user_id)
    history.append({"role": role, "content": content})
    if len(history) > MAX_HISTORY:
        user_histories[user_id] = history[-MAX_HISTORY:]


def ask_groq(user_id: int, user_text: str) -> str:
    """Отправляет запрос в Groq и возвращает ответ со ссылками."""
    add_message(user_id, "user", user_text)

    response = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        max_tokens=1024,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            *get_history(user_id),
        ],
    )

    reply = response.choices[0].message.content
    add_message(user_id, "assistant", reply)

    # ✅ Добавляем ссылки на маркетплейсы
    return append_market_links(reply)


def main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        QUICK_TOPICS,
        resize_keyboard=True,
        input_field_placeholder="Выберите сферу или напишите вопрос...",
    )


# ─── Обработчики команд ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 🖥️\n\n"
        "Я помогу вам выбрать идеальный монитор под вашу сферу деятельности и бюджет.\n\n"
        "👇 Выберите вашу сферу или напишите вопрос:",
        reply_markup=main_keyboard(),
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_histories.pop(update.effective_user.id, None)
    await update.message.reply_text(
        "🗑️ История очищена. Начнём заново!\n\nВыберите сферу деятельности:",
        reply_markup=main_keyboard(),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🖥️ *Бот-эксперт по выбору мониторов*\n\n"
        "Я помогу подобрать монитор для:\n"
        "• 🎮 Геймеров — высокая частота, низкий отклик\n"
        "• 🎨 Дизайнеров — точная цветопередача\n"
        "• 💻 Программистов — комфорт для глаз\n"
        "• 🎬 Видеомонтажёров — широкий цветовой охват\n"
        "• 🏢 Офисной работы — универсальность\n"
        "• 🏗️ Архитекторов/CAD — большая диагональ\n\n"
        "💡 *Советы:*\n"
        "Чем больше деталей вы укажете (бюджет, размер, задачи) — тем точнее рекомендация!\n\n"
        "/clear — начать новый диалог",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name or "Без имени"
    user_text = update.message.text

    logger.info(f"👤 {user_name} ({user_id}): {user_text}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    try:
        reply = ask_groq(user_id, user_text)
        logger.info(f"🤖 Бот → {user_name} ({user_id}): {reply[:100]}...")

        # parse_mode="Markdown" нужен для кликабельных ссылок [текст](url)
        await update.message.reply_text(
            reply,
            reply_markup=main_keyboard(),
            parse_mode="Markdown",
            disable_web_page_preview=True,  # Не показывать превью ссылок
        )

        # Уведомление администратору
        if ADMIN_ID and user_id != ADMIN_ID:
            await context.bot.send_message(
                chat_id=ADMIN_ID,
                text=f"👤 {user_name} (ID: {user_id}):\n{user_text}\n\n🤖 Бот:\n{reply[:300]}..."
            )
    except Exception as e:
        logger.error(f"Ошибка Groq: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка. Попробуйте ещё раз через несколько секунд.",
            reply_markup=main_keyboard(),
        )


# ─── Запуск ───────────────────────────────────────────────────────────────────

def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот запущен! Нажмите Ctrl+C для остановки.")
def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("✅ Бот запущен! Нажмите Ctrl+C для остановки.")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(app.run_polling())


if __name__ == "__main__":
    main()