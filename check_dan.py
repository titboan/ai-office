import asyncio
import asyncpg
import os

async def check():
    conn = await asyncpg.connect(os.getenv('DATABASE_URL'))
    rows = await conn.fetch("""
        SELECT id, assigned_agent, status, chain_id, chain_index,
               created_at, finished_at, error_message
        FROM tasks
        WHERE assigned_agent = 'dan'
        ORDER BY created_at DESC
        LIMIT 5
    """)
    for r in rows:
        print(dict(r))
    await conn.close()

asyncio.run(check())
