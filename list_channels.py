from telethon import TelegramClient
import asyncio
import config

client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)

async def main():
    await client.start(phone=config.PHONE)
    print(f"\n{'ID':<16} {'USERNAME':<28} NAME")
    print("-" * 80)
    async for d in client.iter_dialogs():
        if d.is_channel:
            uname = f"@{d.entity.username}" if getattr(d.entity, "username", None) else "(private)"
            print(f"{d.id:<16} {uname:<28} {d.name}")
    print("\nCopy the @username (or numeric ID for private) into SOURCE_CHANNELS in config.py")

if __name__ == "__main__":
    with client:
        client.loop.run_until_complete(main())