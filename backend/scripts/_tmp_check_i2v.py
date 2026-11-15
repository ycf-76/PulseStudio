import asyncio
import sys

sys.path.insert(0, ".")


async def main() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.config import get_settings

    s = get_settings()
    engine = create_async_engine(s.database_url)
    async with engine.connect() as conn:
        tabs = (await conn.execute(text("show tables"))).fetchall()
        print("tables count:", len(tabs))
        names = [t[0] for t in tabs]
        print("asset-like:", [n for n in names if "asset" in n])

        if "image_assets" in names:
            cols = (await conn.execute(text("show columns from image_assets"))).fetchall()
            print("image_assets cols:", [c[0] for c in cols])
            rows = (await conn.execute(text(
                "select id, original_url from image_assets order by created_at desc limit 6"
            ))).fetchall()
            print("--- recent image_assets ---")
            for r in rows:
                print("  ", r)
    await engine.dispose()


asyncio.run(main())