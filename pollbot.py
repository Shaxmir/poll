import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.filters import Command, CommandStart
import logging
from collections import defaultdict
from operator import itemgetter

# --- Конфиг ---
API_TOKEN = "8065857722:AAEwSlrEEIAtxxBY4WDr04csKrGjXubIBUw"
ADMIN_USER_IDS = (764614936, 997838012)

# --- FSM состояния ---
class PollStates(StatesGroup):
    waiting_for_title = State()
    waiting_for_options = State()
    waiting_for_duration = State()
    waiting_for_winners = State()

# --- Временное хранилище ---
polls = {}  # poll_id: {"title": str, "options": [str], "votes": defaultdict(user_id -> option), "winners": int}
user_votes = defaultdict(dict)  # poll_id: {user_id: option}
message_ids = {}  # poll_id: message_id

# --- Инициализация ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# @dp.message()
# async def get_chat_id(message: Message):
#     await message.answer(f"Chat ID: {message.chat.id}")

# --- Команда /poll ---
@dp.message(Command("poll"))
async def cmd_poll(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_USER_IDS:
        return await message.answer("⛔ Только админ может создавать опросы.")

    await state.set_state(PollStates.waiting_for_title)
    await message.answer("Введите название опроса:")

@dp.message(PollStates.waiting_for_title)
async def poll_title(message: Message, state: FSMContext):
    await state.update_data(title=message.text)
    await state.set_state(PollStates.waiting_for_options)
    await message.answer("Введите варианты через запятую:")

@dp.message(PollStates.waiting_for_options)
async def poll_options(message: Message, state: FSMContext):
    options = [opt.strip() for opt in message.text.split(",") if opt.strip()]
    if len(options) < 2:
        return await message.answer("Нужно хотя бы два варианта.")

    await state.update_data(options=options)
    await state.set_state(PollStates.waiting_for_duration)
    await message.answer("На сколько минут будет длиться опрос? Введите число (например, 1):")

@dp.message(PollStates.waiting_for_duration)
async def poll_duration(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите число (например, 1):")

    duration_minutes = int(message.text)
    await state.update_data(duration=duration_minutes)
    await state.set_state(PollStates.waiting_for_winners)
    await message.answer("Сколько победителей будет? Введите число:")

@dp.message(PollStates.waiting_for_winners)
async def poll_winners(message: Message, state: FSMContext):
    if not message.text.isdigit():
        return await message.answer("Пожалуйста, введите число:")

    winners = int(message.text)
    data = await state.get_data()
    await state.clear()

    poll_id = str(message.message_id)
    polls[poll_id] = {
        "title": data["title"],
        "options": data["options"],
        "votes": defaultdict(int),
        "winners": winners
    }

    buttons = [
        [InlineKeyboardButton(text=f'☑️ {opt}', callback_data=f"vote:{poll_id}:{i}")]
        for i, opt in enumerate(data["options"])
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    GROUP_CHAT_ID = -1002315370924  # замените на ID вашей группы

    sent = await bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=f"🗳️ {data['title']}\nГолосуйте ниже:",
        reply_markup=markup
    )
    await bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id, disable_notification=True)
    message_ids[poll_id] = sent.message_id

    # Запустить таймер на окончание опроса
    asyncio.create_task(finish_poll_after(poll_id, sent.chat.id, data["duration"] * 60))

# --- Обработка голосов ---
@dp.callback_query(F.data.startswith("vote:"))
async def handle_vote(callback: CallbackQuery):
    _, poll_id, index = callback.data.split(":")
    user_id = callback.from_user.id

    if user_id in user_votes[poll_id]:
        return await callback.answer("⛔ Ты уже голосовал.", show_alert=True)

    option = polls[poll_id]["options"][int(index)]
    polls[poll_id]["votes"][option] += 1
    user_votes[poll_id][user_id] = option

    # Обновление текста на кнопках с новым количеством голосов
    buttons = [
        [InlineKeyboardButton(text=f'☑️ {opt} — {polls[poll_id]["votes"].get(opt, 0)} голосов', callback_data=f"vote:{poll_id}:{i}")]
        for i, opt in enumerate(polls[poll_id]["options"])
    ]
    markup = InlineKeyboardMarkup(inline_keyboard=buttons)

    # Обновляем кнопки на оригинальном сообщении
    await callback.message.edit_reply_markup(reply_markup=markup)

    await callback.answer("✅ Вы проголосовали. Спасибо!", show_alert=True)



# --- Завершение опроса ---
async def finish_poll_after(poll_id: str, chat_id: int, delay: int):
    await asyncio.sleep(delay)

    if poll_id not in polls:
        return

    poll = polls[poll_id]
    options = poll["options"]
    votes = poll["votes"]
    winners = poll["winners"]

    # Собираем результаты с учетом 0 голосов
    results = [(option, votes.get(option, 0)) for option in options]
    results.sort(key=itemgetter(1), reverse=True)

    text = f"🥇{poll['title']}\nОпрос окончен!\n\nРезультаты:\n"

    for i, (option, count) in enumerate(results):
        medal = "🥇" if i < winners else "➖"
        text += f"{medal} {option} — {count} голосов\n"

    await bot.send_message(chat_id, text)
    await bot.unpin_chat_message(chat_id=chat_id, message_id=message_ids[poll_id])

    # Удаляем из памяти
    polls.pop(poll_id, None)
    user_votes.pop(poll_id, None)
    message_ids.pop(poll_id, None)



@dp.message(Command("results"))
async def cmd_results(message: Message):
    for poll_id, poll in polls.items():
        options = poll["options"]
        votes = poll["votes"]
        winners = poll["winners"]

        results = [(option, votes.get(option, 0)) for option in options]
        results.sort(key=itemgetter(1), reverse=True)

        text = f"⏳{poll['title']}\nРанний доступ к результатам:\n"
        for i, (option, count) in enumerate(results):
            medal = "🥇" if i < winners else "➖"
            text += f"{medal} {option} — {count} голосов\n"

        await message.answer(text)





#-----Запрос скриншотов для перевода сообщений
from aiogram import F
from aiogram.types import FSInputFile, InputMediaPhoto
from aiogram.utils.media_group import MediaGroupBuilder

@dp.message(F.text.lower().in_({"перевод", "translate", "translation"}))
async def explain_translation(message: Message):
    text = (
        "Вот как можно перевести сообщения других игроков в чате.\n"
        "Here's how you can translate other players' messages in the chat."
    )

    # Отправляем текст
    await message.answer(text)

    # Собираем фото в альбом
    builder = MediaGroupBuilder()
    image_paths = ["images/translation/step1.jpg", "images/translation/step2.jpg", "images/translation/step3.jpg", "images/translation/step4.jpg",]

    for path in image_paths:
        builder.add_photo(media=FSInputFile(path))

    # Отправляем альбом
    await message.answer_media_group(builder.build())


# --- Старт ---
async def main():
    logging.basicConfig(level=logging.INFO)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
