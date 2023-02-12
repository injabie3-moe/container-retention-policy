import asyncio
from sys import argv

from main.main import main

if __name__ == '__main__':
    asyncio.run(main(*argv[1:]))
