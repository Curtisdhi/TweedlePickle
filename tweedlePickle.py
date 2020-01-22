import asyncio
import discord
import os
from discord.ext import commands
from dotenv import load_dotenv
from music import Music

load_dotenv()
token = os.getenv('DISCORD_TOKEN')

bot = commands.Bot(command_prefix=commands.when_mentioned_or("~"),
                   description='Tweddle my pickle.')

music = Music(bot)

@bot.event
async def on_ready():
    print('Logged in as {0} ({0.id})'.format(bot.user))
    print('------')


bot.add_cog(music)
bot.run(token)
