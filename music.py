import asyncio
import os
import discord
from discord.ext import commands
from ytdl_source import YTDLSource
from song import Song

class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.SONG_PATH='songs/'
        self.isPlaying = False
        self.queue = []
        self.currentSong = None

    @commands.command()
    async def join(self, ctx, *, channel: discord.VoiceChannel):
        """Joins a voice channel"""

        if ctx.voice_client is not None:
            return await ctx.voice_client.move_to(channel)

        await channel.connect()

    @commands.command()
    async def play(self, ctx, *, url):
        async with ctx.typing():
            song = await YTDLSource.getMp3FromUrl(url, self.SONG_PATH, self.bot.loop)
            self.queue.append(song)

        if not self.isPlaying:
            await self.playNext(ctx)
        else:
            await ctx.send('Added to queue: **{}**'.format(song.title))

    @commands.command()
    async def volume(self, ctx, volume: int):
        """Changes the player's volume"""

        if ctx.voice_client is None:
            return await ctx.send("Not connected to a voice channel.")

        ctx.voice_client.source.volume = volume / 100
        await ctx.send("Changed volume to {}%".format(volume))

    @commands.command()
    async def stop(self, ctx):
        """Stops and disconnects the bot from voice"""
        self.queue = []
        self.currentSong = None
        self.cleanDownloads()
        await ctx.send("Disconnecting from **{}**.".format(ctx.voice_client.channel))
        await ctx.voice_client.disconnect()

    @commands.command()
    async def queue(self, ctx):
        if len(self.queue) > 1:
            async with ctx.typing():
                await ctx.send('**{}** songs are currently in queue.'.format(len(self.queue)))
                message = '```\n'
                for index, song in enumerate(self.queue):
                    message += '{}: {}\n'.format(index + 1, song.title)
                await ctx.send(message +'```')
        elif self.currentSong is not None:
            await ctx.send('Playing "**{}**" as the last song in queue.'.format(self.currentSong.title))
        else:
            await ctx.send('Song queue is empty.')
              
    @commands.command()
    async def next(self, ctx):
        await self.playNext(self, ctx)

    @play.before_invoke
    async def ensure_voice(self, ctx):
        if ctx.voice_client is None:
            if ctx.author.voice:
                await ctx.author.voice.channel.connect()
            else:
                await ctx.send("You are not connected to a voice channel.")
                raise commands.CommandError("Author not connected to a voice channel.")
        elif ctx.voice_client.is_playing():
            ctx.voice_client.stop()

    async def playNext(self, ctx):
        if len(self.queue) > 0:
            async with ctx.typing():
                self.currentSong = self.queue.pop(0)
                source = await YTDLSource.getSourceFromSong(self.currentSong)
                ctx.voice_client.play(source, 
                    after=lambda e: print('Player error: %s' % e) if e else self.finishedSong(ctx, song))
                self.isPlaying = True
            await ctx.send('Now playing: **{}**'.format(self.currentSong.title))

            if (len(self.queue) > 1):
                await ctx.send('{} songs left in queue.'.format(len(self.queue)))

        else:
            self.currentSong = None
            self.isPlaying = False
            await ctx.send('Song queue is empty.')

    def finishedSong(self, ctx, song):
        os.unlink(song.filepath)
        self.playNext(ctx)

    def cleanDownloads(self):
        for file in os.scandir(self.SONG_PATH):
            os.unlink(file.path)
