import asyncio
import hashlib
import re
import sqlite3
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config

TZ = ZoneInfo(config.TIMEZONE)

# ----------------------------------------------------------------------------
# Relevance gate
# ----------------------------------------------------------------------------
SSC_TERMS = [
    r"\bssc\b", r"\bcgl\b", r"\bchsl\b", r"\bmts\b", r"\bcpo\b", r"\bgd\b",
    r"\bsteno\b", r"\bstenographer\b", r"\bje\b", r"\bchsl\b",
    r"staff selection commission", r"ssc\.gov\.in",
    r"combined graduate level", r"combined higher secondary",
    r"multi tasking", r"selection post", r"delhi police", 
    r"wbpsc", r"wbcs", r"wbsss", r"ibps",
]

AD_TERMS = [
    r"use code", r"\bcoupon\b", r"\bdiscount\b", r"\bbuy now\b",
    r"link in bio", r"\bpromo\b", r"join our (paid|premium)",
    r"telegram premium", r"free pdf", r"click here to join",
]

# ----------------------------------------------------------------------------
# Categorisation — FIRST match wins, so order matters
# ----------------------------------------------------------------------------
CATEGORY_RULES = [
    ("Admit Card / City Intimation", [
        "admit card", "city intimation", "exam city", "exam centre",
        "hall ticket", "intimation slip", "application status",
    ]),
    ("Answer Key", [
        "answer key", "response sheet", "objection", "challenge window",
    ]),
    ("Result", [
        "result", "merit list", "scorecard", "score card",
        "cut off", "cutoff", "final selection",
    ]),
    ("Vacancy", [
        "vacancy", "vacancies", "total posts", "tentative vacancy",
        "revised vacancy", "post details",
    ]),
    ("Deadline", [
        "last date", "closing date", "apply before", "window closes",
        "extended", "extension", "deadline",
    ]),
    ("Notice", [
        "notice", "corrigendum", "public notice", "notification",
        "advertisement", "apply online", "important",
    ]),
]

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
def get_con():
    con = sqlite3.connect(config.DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = get_con()
    con.executescript("""
        CREATE TABLE IF NOT EXISTS messages (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            msg_id           INTEGER,
            channel_id       INTEGER,
            channel_username TEXT,
            channel_title    TEXT,
            date             TEXT,
            text             TEXT,
            category         TEXT,
            hash             TEXT,
            sent             INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_hash ON messages(hash);
        CREATE INDEX IF NOT EXISTS idx_sent ON messages(sent);
    """)
    con.commit()
    con.close()


def is_relevant(text: str) -> bool:
    low = text.lower()
    if not any(re.search(p, low) for p in SSC_TERMS):
        return False
    if any(re.search(p, low) for p in AD_TERMS):
        return False
    return True


def categorize(text: str) -> str:
    low = text.lower()
    for name, kws in CATEGORY_RULES:
        for k in kws:
            if re.search(rf"\b{re.escape(k)}\b", low):
                return name
    return "Other"


def normalize(text: str) -> str:
    t = text.lower()
    t = re.sub(r"https?://\S+", "", t)      # drop URLs
    t = re.sub(r"[^\w\s]", " ", t)          # drop punctuation + emoji
    t = re.sub(r"\s+", " ", t).strip()
    return t


def content_hash(text: str) -> str:
    # Hash the first 250 normalised chars — tolerant of trailing channel footers
    return hashlib.sha1(normalize(text)[:250].encode()).hexdigest()


def snippet(text: str, limit: int = 180) -> str:
    t = re.sub(r"\s+", " ", text).strip()
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0] + "…"
    return t


def extract_url(text: str):
    m = re.search(r"https?://\S+", text)
    return m.group(0).rstrip(").,") if m else None


def item_link(row) -> str | None:
    if row["channel_username"]:
        return f"https://t.me/{row['channel_username']}/{row['msg_id']}"
    return extract_url(row["text"])


# ----------------------------------------------------------------------------
# Digest builder
# ----------------------------------------------------------------------------
def build_digest():
    con = get_con()
    rows = con.execute(
        "SELECT * FROM messages WHERE sent = 0 ORDER BY date ASC"
    ).fetchall()
    con.close()

    if not rows:
        return None, []

    buckets: dict[str, list] = {}
    ids = []
    for r in rows:
        buckets.setdefault(r["category"], []).append(r)
        ids.append(r["id"])

    order = [name for name, _ in CATEGORY_RULES] + ["Other"]
    today = datetime.now(TZ).strftime("%d %b %Y")

    lines = [f"📋 SSC Digest — {today}  ({len(rows)} updates)", ""]

    for cat in order:
        if cat not in buckets:
            continue
        items = buckets[cat]
        shown = items[: config.MAX_ITEMS_PER_CAT]
        lines.append(f"▸ {cat.upper()}")
        for r in shown:
            via = r["channel_title"] or "channel"
            lines.append(f"• {snippet(r['text'])}")
            lines.append(f"   via {via}")
            link = item_link(r)
            if link:
                lines.append(f"   {link}")
        if len(items) > len(shown):
            lines.append(f"   … +{len(items) - len(shown)} more")
        lines.append("")

    lines.append("— Verify at ssc.gov.in before acting on anything.")

    return "\n".join(lines).strip(), ids


def split_message(text: str, limit: int = 3800):
    parts = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        parts.append(text[:cut])
        text = text[cut:].lstrip("\n")
    parts.append(text)
    return parts


# ----------------------------------------------------------------------------
# Sender
# ----------------------------------------------------------------------------
async def send_digest(client: TelegramClient):
    body, ids = build_digest()

    if body is None:
        if config.SEND_EMPTY_DIGEST:
            await client.send_message(
                config.DIGEST_TARGET,
                f"📋 SSC Digest — {datetime.now(TZ).strftime('%d %b %Y')}\n\nNo updates today ✓",
            )
        print("[digest] nothing to send")
        return

    try:
        for chunk in split_message(body):
            await client.send_message(config.DIGEST_TARGET, chunk, link_preview=False)
    except Exception as e:
        print(f"[digest] send failed, will retry next run: {e}")
        return

    con = get_con()
    con.executemany("UPDATE messages SET sent = 1 WHERE id = ?", [(i,) for i in ids])
    con.commit()
    con.close()
    print(f"[digest] sent {len(ids)} items")


# ----------------------------------------------------------------------------
# Startup
# ----------------------------------------------------------------------------
async def resolve_channels(client):
    resolved = []
    print("Resolving channels:")
    for ch in config.SOURCE_CHANNELS:
        try:
            entity = await client.get_entity(ch)
            resolved.append(entity)
            print(f"  ✓ {ch} -> {getattr(entity, 'title', entity)}")
        except Exception as e:
            print(f"  ✗ {ch} -> {e}")
    return resolved


def register_listener(client, entities):
    @client.on(events.NewMessage(chats=entities))
    async def handler(event):
        text = event.message.message or ""
        if len(text) < 20:
            return
        if not is_relevant(text):
            return

        h = content_hash(text)
        con = get_con()
        if con.execute("SELECT 1 FROM messages WHERE hash = ? LIMIT 1", (h,)).fetchone():
            con.close()
            return

        chat = await event.get_chat()
        con.execute(
            """INSERT INTO messages
               (msg_id, channel_id, channel_username, channel_title, date, text, category, hash)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                event.message.id,
                event.chat_id,
                getattr(chat, "username", None),
                getattr(chat, "title", None),
                event.message.date.astimezone(TZ).isoformat(),
                text,
                categorize(text),
                h,
            ),
        )
        con.commit()
        con.close()
        print(f"[{categorize(text)}] {getattr(chat, 'title', '')}: {snippet(text, 60)}")


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------
async def main():
    init_db()
    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE)

    entities = await resolve_channels(client)
    if not entities:
        print("No channels resolved. Fix SOURCE_CHANNELS and rerun.")
        return

    # --- one-shot modes ---
    if "--preview" in sys.argv:
        body, _ = build_digest()
        print("\n" + (body or "(nothing pending)"))
        return

    if "--send-now" in sys.argv:
        await send_digest(client)
        return

    # --- normal mode ---
    register_listener(client, entities)

    scheduler = AsyncIOScheduler(timezone=TZ)
    h, m = map(int, config.DIGEST_TIME.split(":"))
    scheduler.add_job(send_digest, "cron", hour=h, minute=m,
                      args=[client], id="digest")
    scheduler.start()

    print(f"\nListening on {len(entities)} channel(s). "
          f"Digest fires daily at {config.DIGEST_TIME} {config.TIMEZONE}.")
    print("Ctrl+C to stop.\n")

    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())