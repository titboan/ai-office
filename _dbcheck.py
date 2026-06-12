import asyncio, asyncpg, os

async def main():
    internal = os.environ['DATABASE_URL']
    import re
    m = re.match(r'postgresql://([^:]+):([^@]+)@[^/]+/(.+)', internal)
    pub_url = f"postgresql://{m.group(1)}:{m.group(2)}@maglev.proxy.rlwy.net:12614/{m.group(3)}"
    conn = await asyncpg.connect(pub_url)
    print("=== pending_approval reviews ===")
    rows = await conn.fetch(
        "SELECT marketplace, chat_id, status, rating, LEFT(text,60) as t, created_at "
        "FROM marketplace_reviews WHERE status='pending_approval' ORDER BY created_at DESC LIMIT 10"
    )
    for r in rows:
        print(dict(r))
    print("=== shops ===")
    shops = await conn.fetch("SELECT marketplace, chat_id, shop_name FROM marketplace_shops")
    for s in shops:
        print(dict(s))
    await conn.close()

asyncio.run(main())
