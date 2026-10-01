"""Чеки из Telegram-группы: новые фото/файлы -> распознавание -> база.

config.ini (budget/config.ini):
    [telegram]
    api_id = ...
    api_hash = ...
    receipts_chat = https://t.me/+...   ссылка-приглашение группы для чеков
"""
import asyncio
import configparser
import mimetypes
from zoneinfo import ZoneInfo

from core.common import BUDGET, DATA
from core.db import connect, get_meta, set_meta
from receipts import photos

CONFIG = BUDGET / "config.ini"
DOWNLOADS = DATA / "photos" / "telegram"
TZ = ZoneInfo("Europe/Warsaw")
MEDIA_EXT = photos.IMAGE_EXT | {".pdf"}


def config():
    cfg = configparser.ConfigParser()
    if not cfg.read(CONFIG, encoding="utf-8") or not cfg.get("telegram", "api_id", fallback="").strip():
        raise SystemExit(f"Нет {CONFIG} с [telegram] api_id / api_hash / receipts_chat")
    return cfg["telegram"]


async def resolve_chat(client, link: str, con):
    """Ссылка-приглашение -> группа. id запоминается, дальше ищем по нему."""
    from telethon.tl.functions.messages import CheckChatInviteRequest
    from telethon.tl.types import ChatInviteAlready, ChatInvitePeek

    cached = get_meta(con, "tg_receipts_chat_id")
    if cached:
        try:
            return await client.get_entity(int(cached))
        except (ValueError, TypeError):
            pass
    if "/+" in link or "joinchat/" in link:
        invite = await client(CheckChatInviteRequest(link.rstrip("/").split("/")[-1].lstrip("+")))
        if not isinstance(invite, (ChatInviteAlready, ChatInvitePeek)):
            raise SystemExit("Ты не состоишь в этой группе — вступи по ссылке в Telegram и запусти ещё раз.")
        chat = invite.chat
    else:
        chat = await client.get_entity(link)
    set_meta(con, "tg_receipts_chat_id", chat.id)
    con.commit()
    return chat


def media_ext(msg) -> str | None:
    if msg.photo:
        return ".jpg"
    doc = msg.document
    if not doc:
        return None
    name = next((a.file_name for a in doc.attributes if getattr(a, "file_name", None)), "")
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else mimetypes.guess_extension(doc.mime_type or "")
    return ext if ext in MEDIA_EXT else None


def fetch(interactive: bool = True):
    """interactive=False (обновление по расписанию): без входа не спрашиваем телефон, а сообщаем об ошибке."""
    from telethon import TelegramClient

    tg = config()
    con = connect()
    DOWNLOADS.mkdir(parents=True, exist_ok=True)

    async def run():
        client = TelegramClient(str(DATA / "telegram"), int(tg["api_id"]), tg["api_hash"])
        if not interactive:
            await client.connect()
            if not await client.is_user_authorized():
                await client.disconnect()
                raise SystemExit("Нужен вход в Telegram: python budget.py telegram")
        # первый запуск: Telethon спросит в терминале телефон и код из Telegram
        async with client:
            chat = await resolve_chat(client, tg["receipts_chat"], con)
            last = int(get_meta(con, "tg_receipts_last_id", 0))
            new = 0
            async for msg in client.iter_messages(chat, min_id=last, reverse=True):
                ext = media_ext(msg)
                if ext:
                    new += 1
                    path = DOWNLOADS / f"{chat.id}_{msg.id}{ext}"
                    if not path.exists():
                        await client.download_media(msg, file=str(path))
                    hint = msg.date.astimezone(TZ).replace(tzinfo=None).isoformat(timespec="seconds")
                    try:
                        photos.import_file(con, path, hint_date=hint, source_ref=f"telegram:{chat.id}/{msg.id}")
                    except Exception as e:  # noqa: BLE001 — один плохой файл не должен останавливать остальные
                        print(f"  ! сообщение {msg.id}: {e}")
                        continue
                set_meta(con, "tg_receipts_last_id", msg.id)
                con.commit()
            print(f"Telegram: новых файлов с чеками: {new}")

    asyncio.run(run())
