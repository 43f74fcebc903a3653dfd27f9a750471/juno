from wrapper import SharedAPI

async def main():
    api = SharedAPI("api_key")
    user = await api.instagram.story("instagram")
    print(user)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
    