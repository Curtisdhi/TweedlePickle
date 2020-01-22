import discord
from discord.ext import commands
import asyncio
from async_timeout import timeout
from functools import partial
from youtube_dl import YoutubeDL
import re
import urllib.request
import urllib.error
import youtube_dl
import os

#origin: https://gist.github.com/NoirPi/0e1378b868d843a2d6e00180921f35dd

ytdlopts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'
}

ytdl = YoutubeDL(ytdlopts)


class VoiceConnectionError(commands.CommandError):
    """Custom Exception class for connection errors."""


class NotInVoiceChannel(VoiceConnectionError):
    """Exception for cases of invalid Voice Channels."""


class YTDLSource(discord.PCMVolumeTransformer):
    
    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester
        
        self.title = data.get('title')
        self.url = data.get('webpage_url')
        self.alt_title = data.get('alt_title')
        if not data.get('alt_title'):
            self.alt_title = self.title
        self.creator = data.get('creator')
        if not data.get('creator'):
            self.creator = data.get('uploader')
        self.thumbnail = data.get('thumbnail')
    
    def __getitem__(self, item: str):
        """
        Allows us to access attributes similar to a dict.

        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)
    
    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download = False):
        loop = loop or asyncio.get_event_loop()
        
        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)
        
        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]
        
        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {
                "title": data["title"], "url": data["url"], "alt_title":
                    data["alt_title"],
                "uploader": data["uploader"], "creator": data["creator"],
                "duration": data["duration"],
                "view_count": data["view_count"], "like_count": data["like_count"],
                "dislike_count": data["dislike_count"],
                "thumbnail": data["thumbnail"], "webpage_url": data["webpage_url"],
                "requester": ctx.author.name
            }
        
        return cls(discord.FFmpegPCMAudio(source, before_options='-nostdin', options='-vn'),
                   data=data, requester=ctx.author)
    
    @classmethod
    async def regather_stream(cls, data, *, loop):
        """
        Used for preparing a stream, instead of downloading.

        Since Youtube Streaming links expire.
        """
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']
        
        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)
        
        return cls(discord.FFmpegPCMAudio(data['url'], before_options='-nostdin', options='-vn'),
                   data=data, requester=requester)


class MusicPlayer:
    """
    A class which is assigned to each guild using the bot for Music.

    This class implements a queue and loop, which allows for different
    guilds to listen to different playlists
    simultaneously.

    When the bot disconnects from the Voice it's instance will be destroyed.
    """
    
    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current',
                 'np', 'volume', 'repeat', 'repeating')
    
    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog
        
        self.queue = asyncio.Queue()
        self.next = asyncio.Event()
        
        self.np = None  # Now playing message
        self.volume = .5
        self.current = None
        self.repeat = False
        self.repeating = None
        
        ctx.bot.loop.create_task(self.player_loop())
    
    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()
        
        while not self.bot.is_closed():
            vc = self._guild.voice_client
            if len(vc.channel.members) == 1:
                embed = discord.Embed(
                    description="There are no users in the voice channel! Disconnecting...",
                    color=0x1ABC9C
                )
                await self._channel.send(embed=embed)
                self.destroy(self._guild)
            self.next.clear()
            
            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    if self.repeat and self.current is not None:
                        source = self.repeating
                    else:
                        source = await self.queue.get()
                        self.repeating = source
            except asyncio.TimeoutError:
                return self.destroy(self._guild)
            
            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```')
                    continue
            
            source.volume = self.volume
            self.current = source
            
            self._guild.voice_client.play(source,
                                          after=lambda _: self.bot.loop.call_soon_threadsafe(
                                              self.next.set))
            embed = discord.Embed(title="Now Playing", description=source.alt_title,
                                  color=0x1ABC9C)
            embed.add_field(name="Requested By", value=source.requester)
            embed.set_thumbnail(url=vc.source.thumbnail)
            self.np = await self._channel.send(embed=embed)
            await self.next.wait()
            
            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            
            try:
                # We are no longer playing this song...
                await self.np.delete()
            except discord.HTTPException:
                pass
    
    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))


class Music(commands.Cog):
    """Provides Music Playback Functionality. User must be in a voice channel."""
    
    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.name = "Music"
    
    async def cog_check(self, ctx):
        if not ctx.author.voice:
            embed = discord.Embed(
                description="You must join a voice channel to be able to use this command!",
                color=0x1ABC9C
            )
            await ctx.send(embed=embed)
            return False
        elif not ctx.guild:
            embed = discord.Embed(
                description="You must be in a guild in order to use these commands!",
                color=0x1ABC9C
            )
            await ctx.send(embed=embed)
            return False
        return True
    
    async def cleanup(self, guild):
        await guild.voice_client.disconnect()
        
        try:
            del self.players[guild.id]
        except KeyError:
            pass
    
    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player
        
        return player
    
    def gather_playlist(self, url):
        sTUBE = ''
        cPL = ''
        amp = 0
        final_url = []
        
        if 'list=' in url:
            eq = url.rfind('=') + 1
            cPL = url[eq:]
        
        else:
            return [url]
        
        try:
            yTUBE = urllib.request.urlopen(url).read()
            sTUBE = str(yTUBE)
        except urllib.error.URLError as e:
            print(e.reason)
        
        tmp_mat = re.compile(r'watch\?v=\S+?list=' + cPL)
        mat = re.findall(tmp_mat, sTUBE)
        
        if mat:
            
            for PL in mat:
                yPL = str(PL)
                if '&' in yPL:
                    yPL_amp = yPL.index('&')
                final_url.append('http://www.youtube.com/' + yPL[:yPL_amp])
            
            all_url = list(set(final_url))
            return all_url
        
        else:
            return [url]
    
    @commands.command(aliases=["join"])
    async def summon(self, ctx):
        try:
            channel = ctx.author.voice.channel
        except AttributeError:
            raise NotInVoiceChannel('You are not currently in a voice channel!')
        
        vc = ctx.voice_client
        out = f"Connected to: {channel}"
        if vc:
            if vc.channel.id == channel.id:
                out = "I'm already in that channel!"
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                out = f'Moving to channel: <{channel}> timed out.'
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                out = f'Connecting to channel: <{channel}> timed out.'
        
        embed = discord.Embed(description=out, color=0x1ABC9C)
        await ctx.send(embed=embed, delete_after=20)
    
    @commands.command()
    async def play(self, ctx, *, search):
        
        if not len(ctx.message.embeds) == 1 and "https://" in search:
            return
        
        if not ctx.voice_client:
            await ctx.invoke(self.summon)
        
        if ctx.author in ctx.voice_client.channel.members:
            player = self.get_player(ctx)
            playlist = self.gather_playlist(search)
            if len(playlist) > 1:
                embed = discord.Embed(
                    description=f"Added {len(playlist)} songs to the Queue!",
                    color=0x1ABC9C
                )
                await ctx.send(embed=embed)
                for track in playlist:
                    try:
                        source = await YTDLSource.create_source(ctx, track, loop=self.bot.loop,
                                                                download=True)
                        await player.queue.put(source)
                    except:
                        pass
            else:
                source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop,
                                                        download=True)
                await player.queue.put(source)
                embed = discord.Embed(
                    title=f"Added to the queue!",
                    description=f"[{source['alt_title']} - {source['creator']}]({source['url']})",
                    color=0x1ABC9C
                )
                embed.set_thumbnail(url=source["thumbnail"])
                await ctx.send(embed=embed)
    
    @commands.command(brief="Pauses the current song.")
    async def pause(self, ctx):
        """Pause the currently playing song."""
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            out = f'{ctx.author}: Paused the song!'
            if not vc or not vc.is_playing():
                out = "I'm not currently playing anything!"
            elif vc.is_paused():
                out = "The player is already paused!"
            vc.pause()
            await ctx.send(embed=discord.Embed(description=out, color=0x1ABC9C))
    
    @commands.command(brief="Resumes the current song.")
    async def resume(self, ctx):
        """Resume the currently paused song."""
        vc = ctx.voice_client
        out = f'{ctx.author}: Resumed the song!'
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                out = 'I am not currently playing anything!'
            elif not vc.is_paused():
                out = "The player is not currently paused!"
        
        vc.resume()
        await ctx.send(embed=discord.Embed(description=out, color=0x1ABC9C))
    
    @commands.command(aliases=["fs"], brief="Forceskips the song!")
    @commands.has_permissions(manage_guild=True)
    async def forceskip(self, ctx):
        """
        Force skips the song!

        Requires "manage_guild" perms!
        """
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                embed = discord.Embed(
                    description="I am not currently playing anything!",
                    color=0x1ABC9C
                )
                return await ctx.send(embed=embed, delete_after=20)
            if vc.is_paused():
                pass
            elif not vc.is_playing():
                return
        if vc:
            vc.stop()
    
    @commands.command(brief="Skips the song.")
    async def skip(self, ctx):
        """Skip the currently playing song."""
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                embed = discord.Embed(
                    title="Music Player",
                    description="I am not currently playing anything!",
                    color=0x1ABC9C
                )
                return await ctx.send(embed=embed, delete_after=20)
            if vc.is_paused():
                pass
            elif not vc.is_playing():
                return
            
            def stop():
                vc.stop()
            
            if len(vc.channel.members) > 2:
                embed = discord.Embed(
                    title="Music Player",
                    description=f"""
        {ctx.author.mention} has requested the current song be skipped!
        If a majority vote is reached, I will skip this track!""",
                    color=0x1ABC9C
                )
                msg = await ctx.send(embed=embed)
                await msg.add_reaction("✅")
                await asyncio.sleep(1)
                await msg.add_reaction("❎")
                await asyncio.sleep(1)
                pro = 0
                against = 0
                total = len(vc.channel.members) - 1
                
                def check(r, u):
                    return u in vc.channel.members
                
                try:
                    for i in range(120):
                        reaction, user = await self.bot.wait_for("reaction_add", check=check)
                        if pro >= total * .75:
                            stop()
                            break
                        elif against >= total * .75:
                            raise Exception
                        elif str(reaction.emoji) == "✅":
                            pro = pro + 1
                        elif str(reaction.emoji) == "❎":
                            against = against + 1
                        await asyncio.sleep(1)
                    emebd = discord.Embed(
                        title="Music Player",
                        description="A majority was reached! Skipping...",
                        color=0x1ABC9C
                    )
                except Exception as e:
                    embed = discord.Embed(
                        description="A majority vote was not reached!",
                        color=0x1ABC9C
                    )
                await msg.edit(embed=embed, delete_after=20)
            else:
                embed = discord.Embed(
                    description="The song has been skipped!",
                    color=0x1ABC9C
                )
                await ctx.send(embed=embed, delete_after=20)
                stop()
    
    @commands.command(aliases=['q'], brief="Provides queued songs")
    async def queue(self, ctx):
        """Provides a list of upcoming songs!"""
        vc = ctx.voice_client
        
        if not vc or not vc.is_connected():
            return await ctx.send(
                embed=discord.Embed(description='I am not currently connected to voice!',
                                    color=0x1ABC9C), delete_after=20)
        
        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send(
                embed=discord.Embed(description='There are currently no more queued songs.',
                                    color=0x1ABC9C))
        
        text = "\n\n".join(i["alt_title"] for i in player.queue._queue)
        
        await self.bot.Pager.embed_generator_send(ctx, text, lines=20,
                                                  title=f'In Queue - {len(player.queue._queue)}')
    
    @commands.command(aliases=['np'], brief="Displays the current song")
    async def playing(self, ctx):
        """Display information about the currently playing song."""
        vc = ctx.voice_client
        
        if not vc or not vc.is_connected():
            return await ctx.send(
                embed=discord.Embed(description='I am not currently connected to voice!',
                                    color=0x1ABC9C), delete_after=20)
        
        player = self.get_player(ctx)
        if not player.current:
            return await ctx.send(
                embed=discord.Embed(description='I am not currently playing anything!',
                                    color=0x1ABC9C))
        
        try:
            # Remove our previous now_playing message.
            await player.np.delete()
        except discord.HTTPException:
            pass
        
        embed = discord.Embed(title="Now Playing", description=vc.source.alt_title,
                              color=0x1ABC9C)
        embed.add_field(name="Requested By", value=vc.source.requester)
        embed.set_thumbnail(url=vc.source.thumbnail)
        player.np = await ctx.send(embed=embed)
    
    @commands.command(aliases=['vol'], brief="Changes the player volume!")
    async def volume(self, ctx, *, vol: float):
        """Change the player volume. Please specify a value between 1 and 100!"""
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                return await ctx.send(
                    embed=discord.Embed(description='I am not currently connected to voice!',
                                        color=0x1ABC9C),
                    delete_after=20)
            
            if not 0 < vol < 101:
                return await ctx.send(
                    embed=discord.Embed(description='Please enter a value between 1 and 100.',
                                        color=0x1ABC9C),
                    delete_after=20)
            
            player = self.get_player(ctx)
            
            if vc.source:
                vc.source.volume = vol / 100
            
            player.volume = vol / 100
            await ctx.send(
                embed=discord.Embed(description=f'{ctx.author}: Set the volume to {vol}%',
                                    color=0x1ABC9C))
    
    @commands.command(brief="Changes the player volume!")
    async def repeat(self, ctx):
        """Repeats the currently playing song"""
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                return await ctx.send(
                    embed=discord.Embed(description='I am not currently connected to voice!',
                                        color=0x1ABC9C),
                    delete_after=20)
            try:
                player = self.get_player(ctx)
                
                if player.repeat:
                    player.repeat = False
                    out = f"The song {vc.source.title} is no longer on repeat!"
                else:
                    player.repeat = True
                    out = f"The song {vc.source.title} is now on repeat!"
            
            except AttributeError:
                out = "There is not currently a song playing!"
            embed = discord.Embed(description=out, color=0x1ABC9C)
            await ctx.send(embed=embed)
    
    @commands.command(aliases=["destroy"], brief="Stops and kills the player!")
    async def stop(self, ctx):
        """Stop the currently playing song and destroy the player."""
        vc = ctx.voice_client
        if vc is not None and ctx.author in vc.channel.members:
            if not vc or not vc.is_connected():
                embed = discord.Embed(
                    description='I am not currently playing anything!',
                    color=0x1ABC9C
                )
                return await ctx.send(embed=embed)
            await self.cleanup(ctx.guild)
            embed = discord.Embed(
                description="The player has been stopped!",
                color=0x1ABC9C
            )
            await ctx.send(embed=embed)


def setup(bot):
    bot.add_cog(Music(bot))
