import asyncio

class Song:
    url = ''
    id = ''
    title = ''
    filepath = ''
    data = {}

    def __init__(self, url, data, filepath):
        self.url = url
        self.id = data['id']
        self.title = data['title']
        self.data = data
        self.filepath = filepath