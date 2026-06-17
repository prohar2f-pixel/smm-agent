import feedparser
from agent.memory import add_signal

RSS_SOURCES = [
    "https://habr.com/ru/rss/hubs/all/",
    "https://vc.ru/rss",
    "https://tproger.ru/feed/",
]


async def fetch_rss():
    for url in RSS_SOURCES:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:
                title = entry.get("title", "").strip()
                if title:
                    await add_signal(
                        source=url,
                        source_type="rss",
                        summary=title,
                        signal_type="topic",
                    )
        except Exception:
            pass
