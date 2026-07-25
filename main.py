import logging
import os
import re
from datetime import datetime

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    ConversationHandler,
    Filters,
    MessageHandler,
    Updater,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("bsc-alert-bot")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "BNB")
API_KEY = os.getenv("ETHERSCAN_API_KEY") or os.getenv("BSCSCAN_API_KEY", "BNB")
CHAIN_ID = os.getenv("ETHERSCAN_CHAIN_ID", "56")
ETHERSCAN_API_URL = "https://api.etherscan.io/v2/api"

WAITING_ADDRESS = 1
ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")

# chat_id -> set of watched addresses
user_addresses = {}


def main_menu_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Set address", callback_data="set_address"),
                InlineKeyboardButton("Unset address", callback_data="unset_menu"),
            ],
            [InlineKeyboardButton("My addresses", callback_data="list_addresses")],
        ]
    )


def cancel_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Cancel", callback_data="cancel")]]
    )


def format_addresses(chat_id):
    addresses = sorted(user_addresses.get(chat_id, set()))
    if not addresses:
        return "No addresses are being watched."
    lines = ["Watched addresses:"]
    lines.extend(f"• `{addr}`" for addr in addresses)
    return "\n".join(lines)


def start(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    text = (
        "BSC transaction alert bot\n\n"
        "Use the buttons below to manage wallet addresses.\n\n"
        f"{format_addresses(chat_id)}"
    )
    update.message.reply_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


def menu_command(update: Update, context: CallbackContext):
    start(update, context)


def list_addresses_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    query.edit_message_text(
        format_addresses(chat_id),
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


def set_address_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    query.edit_message_text(
        "Send the BSC wallet address to watch.\n"
        "Example: `0x8894E0a0c962CB723c1976a4421c95949bE2D4E3`",
        reply_markup=cancel_keyboard(),
        parse_mode="Markdown",
    )
    return WAITING_ADDRESS


def receive_address(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    address = (update.message.text or "").strip()

    if not ADDRESS_RE.match(address):
        update.message.reply_text(
            "Invalid address. Send a valid BSC address like:\n"
            "`0x` + 40 hex characters",
            reply_markup=cancel_keyboard(),
            parse_mode="Markdown",
        )
        return WAITING_ADDRESS

    address = address.lower()
    user_addresses.setdefault(chat_id, set()).add(address)
    log.info("Address set for chat_id=%s address=%s", chat_id, address)
    update.message.reply_text(
        f"Address `{address}` added.\n\n{format_addresses(chat_id)}",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


def unset_menu_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    addresses = sorted(user_addresses.get(chat_id, set()))

    if not addresses:
        query.edit_message_text(
            "No addresses to unset.",
            reply_markup=main_menu_keyboard(),
        )
        return

    buttons = [
        [InlineKeyboardButton(f"Unset {addr[:10]}…{addr[-6:]}", callback_data=f"unset:{addr}")]
        for addr in addresses
    ]
    buttons.append([InlineKeyboardButton("Unset all", callback_data="unset_all")])
    buttons.append([InlineKeyboardButton("Back", callback_data="list_addresses")])
    query.edit_message_text(
        "Choose an address to unset:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


def unset_one_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    address = query.data.split(":", 1)[1]
    watched = user_addresses.get(chat_id, set())

    if address in watched:
        watched.remove(address)
        if not watched:
            user_addresses.pop(chat_id, None)
        log.info("Address unset for chat_id=%s address=%s", chat_id, address)
        msg = f"Removed `{address}`.\n\n{format_addresses(chat_id)}"
    else:
        msg = f"Address `{address}` was not being watched.\n\n{format_addresses(chat_id)}"

    query.edit_message_text(msg, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


def unset_all_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    removed = user_addresses.pop(chat_id, set())
    log.info("Unset all addresses for chat_id=%s count=%s", chat_id, len(removed))
    query.edit_message_text(
        f"Removed {len(removed)} address(es).\n\n{format_addresses(chat_id)}",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )


def cancel_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    query.edit_message_text(
        f"Cancelled.\n\n{format_addresses(chat_id)}",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


def cancel_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    update.message.reply_text(
        f"Cancelled.\n\n{format_addresses(chat_id)}",
        reply_markup=main_menu_keyboard(),
        parse_mode="Markdown",
    )
    return ConversationHandler.END


def set_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if context.args:
        address = context.args[0].strip()
        if not ADDRESS_RE.match(address):
            update.message.reply_text(
                "Invalid address. Use `/set 0x...` or the Set address button.",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
            return ConversationHandler.END
        address = address.lower()
        user_addresses.setdefault(chat_id, set()).add(address)
        log.info("Address set for chat_id=%s address=%s", chat_id, address)
        update.message.reply_text(
            f"Address `{address}` added.\n\n{format_addresses(chat_id)}",
            reply_markup=main_menu_keyboard(),
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    update.message.reply_text(
        "Send the BSC wallet address to watch.",
        reply_markup=cancel_keyboard(),
    )
    return WAITING_ADDRESS


def unset_command(update: Update, context: CallbackContext):
    chat_id = update.effective_chat.id
    if context.args:
        address = context.args[0].strip().lower()
        watched = user_addresses.get(chat_id, set())
        if address in watched:
            watched.remove(address)
            if not watched:
                user_addresses.pop(chat_id, None)
            update.message.reply_text(
                f"Removed `{address}`.\n\n{format_addresses(chat_id)}",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
        else:
            update.message.reply_text(
                f"Address `{address}` was not being watched.\n\n{format_addresses(chat_id)}",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown",
            )
        return

    addresses = sorted(user_addresses.get(chat_id, set()))
    if not addresses:
        update.message.reply_text("No addresses to unset.", reply_markup=main_menu_keyboard())
        return

    buttons = [
        [InlineKeyboardButton(f"Unset {addr[:10]}…{addr[-6:]}", callback_data=f"unset:{addr}")]
        for addr in addresses
    ]
    buttons.append([InlineKeyboardButton("Unset all", callback_data="unset_all")])
    buttons.append([InlineKeyboardButton("Back", callback_data="list_addresses")])
    update.message.reply_text("Choose an address to unset:", reply_markup=InlineKeyboardMarkup(buttons))


def check_transactions(context: CallbackContext):
    if not user_addresses:
        log.info("No watched addresses yet. Open the bot and use Set address.")
        return

    total = sum(len(addrs) for addrs in user_addresses.values())
    log.info("Checking transactions for %s address(es) across %s chat(s)", total, len(user_addresses))

    for chat_id, addresses in list(user_addresses.items()):
        for address in list(addresses):
            try:
                params = {
                    "chainid": CHAIN_ID,
                    "module": "account",
                    "action": "txlist",
                    "address": address,
                    "startblock": 0,
                    "endblock": 99999999,
                    "page": 1,
                    "offset": 100,
                    "sort": "desc",
                    "apikey": API_KEY,
                }

                response = requests.get(ETHERSCAN_API_URL, params=params, timeout=30)
                if response.status_code != 200:
                    log.error("HTTP %s from Etherscan for %s", response.status_code, address)
                    continue

                data = response.json()
                if str(data.get("status")) != "1":
                    log.warning(
                        "Etherscan API error for %s: %s %s",
                        address,
                        data.get("message"),
                        data.get("result"),
                    )
                    continue

                transactions = data.get("result", [])
                if not isinstance(transactions, list):
                    log.warning("Unexpected Etherscan result for %s: %s", address, transactions)
                    continue

                log.info("Fetched %s txs for %s", len(transactions), address)
                for tx in transactions:
                    transaction_amount = int(tx["value"]) / 1e18
                    if transaction_amount > 10000:
                        transaction_date = datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%Y-%m-%d")
                        transaction_time = datetime.utcfromtimestamp(int(tx["timeStamp"])).strftime("%H:%M:%S")
                        sender_address = tx["from"]
                        receiver_address = tx["to"]
                        transaction_hash = tx["hash"]
                        transaction_link = f"https://bscscan.com/tx/{transaction_hash}"

                        message = (
                            f"Watched: `{address}`\n"
                            f"Transaction amount: {transaction_amount} BNB\n"
                            f"Transaction date: {transaction_date}\n"
                            f"Transaction time: {transaction_time}\n"
                            f"Sender Address: `{sender_address}`\n"
                            f"Receiver Address: `{receiver_address}`\n"
                            f"Transaction Hash: `{transaction_hash}`\n"
                            f"Transaction Link: {transaction_link}"
                        )
                        context.bot.send_message(chat_id, message, parse_mode="Markdown")
                        log.info("Alert sent to chat_id=%s hash=%s", chat_id, transaction_hash)
            except Exception as e:
                log.exception("Error checking transactions for %s: %s", address, e)


def main():
    if not TOKEN or TOKEN == "BNB":
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing or still set to placeholder")
    if not API_KEY or API_KEY == "BNB":
        raise SystemExit("ETHERSCAN_API_KEY is missing or still set to placeholder")

    log.info("Starting BSC alert bot")
    log.info("Etherscan V2 chainid=%s", CHAIN_ID)
    log.info("Telegram token loaded (%s...)", TOKEN[:8])
    log.info("Etherscan API key loaded (%s...)", API_KEY[:6])

    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher

    set_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(set_address_callback, pattern=r"^set_address$"),
            CommandHandler("set", set_command),
        ],
        states={
            WAITING_ADDRESS: [
                MessageHandler(Filters.text & ~Filters.command, receive_address),
                CallbackQueryHandler(cancel_callback, pattern=r"^cancel$"),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CallbackQueryHandler(cancel_callback, pattern=r"^cancel$"),
        ],
        allow_reentry=True,
    )

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("menu", menu_command))
    dp.add_handler(CommandHandler("unset", unset_command))
    dp.add_handler(set_conv)
    dp.add_handler(CallbackQueryHandler(list_addresses_callback, pattern=r"^list_addresses$"))
    dp.add_handler(CallbackQueryHandler(unset_menu_callback, pattern=r"^unset_menu$"))
    dp.add_handler(CallbackQueryHandler(unset_all_callback, pattern=r"^unset_all$"))
    dp.add_handler(CallbackQueryHandler(unset_one_callback, pattern=r"^unset:0x[a-fA-F0-9]{40}$"))

    updater.job_queue.run_repeating(check_transactions, interval=30, first=0)
    log.info("Polling Telegram and checking txs every 30s")
    updater.start_polling()
    log.info("Bot is running")
    updater.idle()


if __name__ == "__main__":
    main()
