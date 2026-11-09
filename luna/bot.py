import asyncio
import logging
from contextlib import asynccontextmanager

from telethon import events
from telethon.errors import RPCError
from telethon.tl.functions.messages import SetTypingRequest
from telethon.tl.types import MessageMediaWebPage, SendMessageTypingAction

from luna.arq import BUSY_TEXT, MAX_QUERY_CHARS, LunaError
from luna.limits import FULL, USER_WAITING, WaitingRequests
from luna.routing import command_name, replied_message_id, topic_id

logger = logging.getLogger(__name__)

# Telegram's limit for the text of one message
MAX_MESSAGE_CHARS = 4096

# Telegram typing indicator interval
TYPING_EVERY_SECONDS = 4

REPO_TEXT = (
    "[KANAL](https://t.me/adsizislam)"
    " | [TURK](https://t.me/turksanalci)"
)

HELP_TEXT = (
    "Merhaba, ben Minnak! Benimle özelden veya gruplarda sohbet edebilirsin. "
    "Gruplarda Minnak dediğinde, beni etiketlediğinde veya mesajıma yanıt "
    "verdiğinde sana cevap veririm.\n\n"
    "/repo - Kaynak kodum"
)

TOO_LONG_TEXT = (
    f"Mesajın çok uzun. En fazla {MAX_QUERY_CHARS} karakter gönderebilirsin."
)

# Commands and replies
COMMAND_REPLIES = {
    "start": HELP_TEXT,
    "help": HELP_TEXT,
    "repo": REPO_TEXT,
}


def has_media(message) -> bool:
    """Photos, stickers, files... A link preview is not media here."""
    return message.media is not None and not isinstance(
        message.media, MessageMediaWebPage
    )


async def send_reply(event, text: str, **options):
    """Reply to the message."""
    try:
        await event.reply(text, link_preview=False, **options)
    except RPCError as error:
        logger.warning(
            "Can't reply in chat %s: %s",
            event.chat_id,
            error
        )


async def keep_typing(client, chat, topic):
    """Show typing indicator until cancelled."""
    request = SetTypingRequest(
        chat,
        SendMessageTypingAction(),
        top_msg_id=topic
    )

    try:
        while True:
            await client(request)
            await asyncio.sleep(TYPING_EVERY_SECONDS)

    except (RPCError, ConnectionError) as error:
        logger.debug("Can't show typing: %s", error)


@asynccontextmanager
async def typing(client, chat, topic):
    task = asyncio.create_task(
        keep_typing(client, chat, topic)
    )

    try:
        yield
    finally:
        task.cancel()


class Luna:

    def __init__(self, client, arq, me, waiting: WaitingRequests = None):
        """me: the bot's own user, from client.get_me()"""

        self.client = client
        self.arq = arq
        self.bot_id = me.id
        self.username = me.username
        self.waiting = waiting or WaitingRequests()

    def attach(self):
        self.client.add_event_handler(
            self.on_message,
            events.NewMessage(incoming=True)
        )

    async def on_message(self, event):

        # Private chats and groups, not channel posts
        if not (event.is_private or event.is_group):
            return

        message = event.message
        text = (message.message or "").strip()

        if not text or has_media(message):
            return

        command = command_name(text, self.username)

        if command is not None:
            await self.run_command(event, command)
            return

        if event.is_private or await self.is_for_luna(event, text):
            await self.answer(event, text)

    async def run_command(self, event, command: str):

        reply = COMMAND_REPLIES.get(command)

        if reply is None:
            return

        await send_reply(event, reply)

    async def is_for_luna(self, event, text: str) -> bool:
        """A group message containing Minnak, mentioning the bot
        or replying to one of its messages."""

        # Minnak keyword detection (case-insensitive)
        if event.message.mentioned or "minnak" in text.casefold():
            return True

        # Check whether the message is a reply to the bot
        replied_id = replied_message_id(
            event.message.reply_to
        )

        if replied_id is None:
            return False

        try:
            replied = await self.client.get_messages(
                await event.get_input_chat(),
                ids=replied_id
            )

        except RPCError as error:
            logger.warning(
                "Can't read the replied message in chat %s: %s",
                event.chat_id,
                error
            )
            return False

        return (
            replied is not None
            and replied.sender_id == self.bot_id
        )

    async def answer(self, event, text: str):
        """Send the text to Minnak and reply with the answer."""

        if len(text) > MAX_QUERY_CHARS:
            await send_reply(event, TOO_LONG_TEXT)
            return

        # ARQ remembers the conversation per user
        user_id = event.sender_id or event.chat_id

        status = self.waiting.add(user_id)

        if status == USER_WAITING:
            return

        if status == FULL:
            await send_reply(event, BUSY_TEXT)
            return

        try:
            reply = await self.ask(event, text, user_id)

        finally:
            self.waiting.remove(user_id)

        # Plain text response
        await send_reply(
            event,
            reply[:MAX_MESSAGE_CHARS],
            parse_mode=None
        )

    async def ask(self, event, text: str, user_id: int) -> str:
        """Minnak's answer with typing indicator."""

        chat = await event.get_input_chat()

        try:
            async with typing(
                self.client,
                chat,
                topic_id(event.message.reply_to)
            ):
                return await self.arq.luna(text, user_id)

        except LunaError as error:
            return str(error)
