from django.core.management.base import BaseCommand
from decouple import config
import asyncio
from telegram import Bot

class Command(BaseCommand):
    help = 'Tests the Telegram Bot Connection'

    def handle(self, *args, **kwargs):
        token = config('TELEGRAM_TOKEN')

        async def main():
            bot = Bot(token=token)
            me = await bot.get_me()
            self.stdout.write(self.style.SUCCESS('Connection Successful!'))
            self.stdout.write(f'Bot Name: {me.first_name}')
            self.stdout.write(f'Bot Username: @{me.username}')

        asyncio.run(main())
