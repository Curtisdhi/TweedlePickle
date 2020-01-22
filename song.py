import asyncio

class Song:

    def __init__(self, context, url, data, filepath):
        self.context = context
        self.url = url
        self.id = data['id']
        self.title = data['title']
        self.data = data
        self.filepath = filepath