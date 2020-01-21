import os
import discord
import youtube_dl
from song import Song

QUALITY = 192

# Suppress noise about console usage from errors
youtube_dl.utils.bug_reports_message = lambda: ''

ytdl_format_options = {
    'format': 'bestaudio/best',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0' # bind to ipv4 since ipv6 addresses cause issues sometimes

}

ffmpeg_options = {
    'options': '-vn'
}

ytdl = youtube_dl.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, song, volume=0.5):
        super().__init__(source, volume)

        self.data = song.data

        self.title = song.title
        self.url = song.url

    @classmethod
    async def getSourceFromSong(cls, song):
        return cls(discord.FFmpegPCMAudio(song.filepath, **ffmpeg_options), song=song)

    async def getMp3FromUrl(url, dest, loop):
        cwd = os.getcwd()
        os.chdir(dest)    
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=True))
        os.chdir(cwd)
        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        filename = ytdl.prepare_filename(data)
        filepath = dest +''+ filename

        return Song(url, data, filepath)
