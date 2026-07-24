---
title: "Collision-Free URL Shortening with PostgreSQL Sequences and Base62"
date: "2025-07-01"
summary: "How Snip generates unique short codes without UUIDs, retry loops, or database-level locking. The flush bug that only appeared under load."
pinned: false
---

# Collision-Free URL Shortening with PostgreSQL Sequences and Base62

## Why Not Just Use UUIDs?

Most URL shorteners either generate random short codes and check for collisions with retry loops, or use UUIDs truncated to some length. Both approaches have problems.

Random generation with retry loops means you are doing extra database queries on every write. Under concurrent load, two requests can both check "is this code taken?", both get "no", and both try to insert, causing one to fail and retry. This is a classic TOCTOU (time-of-check-time-of-use) race condition. The retry loop masks the problem but does not fix it. Under high concurrency, the retry count increases and the latency spikes.

UUIDs avoid collisions by being statistically unique (122 bits of randomness), but they are long. A UUIDv4 is 36 characters. Even truncated, you lose the original collision guarantees and still end up with identifiers that do not carry ordering information. You also cannot sort UUIDs chronologically, which makes analytics harder. If you want to know "which short link was created first?", you cannot tell from the code alone.

I wanted something different for Snip: every short code generated deterministically from a unique database sequence, with no application-level retry loops or collision checks.

## The Approach: PostgreSQL Sequence + Base62

The idea is simple. Use an auto-incrementing integer as the primary key, then encode that integer in Base62. The result is your short code.

Base62 uses the characters `0-9a-zA-Z`, giving 62 possible values per character. A 7-character Base62 string can represent 62 to the power of 7, which is roughly 3.5 trillion unique values. The first URL gets code `1`, the second `2`, and so on. Since the counter only goes up, collisions are impossible by definition.

The database model reflects this design:

```python
class Link(Base):
    id = mapped_column(primary_key=True)
    original_url = mapped_column(String)
    short_code = mapped_column(String, unique=True)
```

The unique=True constraint is still valuable as a final database guarantee, but collisions are not expected because the value is derived from the primary key sequence.

The id column is the source of uniqueness. PostgreSQL assigns it through its sequence generator, and that value becomes the input to the Base62 encoder:

```
Database ID → Base62 encoding → Short code

1          → "1"
1000       → "g8"
123456789  → "8M0kL"

```

The critical part is the counter. In PostgreSQL, an auto-incrementing primary key gives you this guarantee at the database level. The sequence generator handles concurrency. PostgreSQL assigns sequence values atomically, so concurrent inserts receive distinct IDs.

The encoding function is pure math, no state, no side effects, no database calls. It runs in O(log n) time where n is the counter value. A counter value of 123 million encodes to a 5-character string.

## The Bug That Only Appeared Under Load

This is where the integration tests proved their worth. I wrote a test suite that simulates concurrent URL shortening requests. Without these tests, everything passed in normal sequential testing.

The bug was in how I was reading the counter back after insert. In async SQLAlchemy, if you do not `await` the session flush before reading the auto-generated ID, you can get `None` back or a stale cached value. Under sequential testing this almost never triggers because the session has time to sync. Under concurrent load with connection pooling, it triggers regularly.

The fix was explicitly flushing the session after insert and before encoding. Here is the actual create route in Snip:

```python
# app/routers/links.py
@router.post("/links/")
async def create_link(request: Request, link: Annotated[LinkCreate, Form()], db: SessionDep):
    link.original_url = str(link.original_url)

    # Duplicate detection: return existing short link if URL was already shortened
    results = await db.execute(select(Link).where(Link.original_url == link.original_url))
    existing = results.scalars().first()

    if existing:
        short_link = f"{request.base_url}{existing.short_code}"
        return templates.TemplateResponse(request=request, name="result.html", context={"link": short_link})

    new_link = Link(original_url=link.original_url)
    db.add(new_link)
    await db.flush()  # forces PostgreSQL to assign the ID

    new_link.short_code = convert_to_shortcode(new_link.id)
    await db.commit()

    short_link = f"{request.base_url}{new_link.short_code}"
    return templates.TemplateResponse(request=request, name="result.html", context={"link": short_link})
```

The `await db.flush()` call is the key line. It sends the INSERT to PostgreSQL and gets back the auto-generated ID, all without committing the transaction. The short code is then computed from that ID and set on the model before the commit. This guarantees the short code is always correct, even under concurrent load.

Duplicate URL detection is a SELECT query before insert. If the same long URL was already shortened, the existing short code is returned directly. This keeps the database clean and means all clicks on the same long URL are tracked under one short link. The duplicate check is an optimization, not the uniqueness guarantee. In production, the database constraint remains the final authority if two identical submissions arrive concurrently.

That flush bug was one of several that only surfaced under concurrent conditions. Missing commits on write operations, race conditions in duplicate detection, and query patterns that leaked information under timing. All caught by integration tests that ran the full request cycle, not isolated unit tests. The test suite uses httpx.AsyncClient against the ASGI app and runs the full request lifecycle instead of isolated unit tests. SQLite keeps the feedback loop fast, while PostgreSQL-backed testing covers database-specific behavior.

## Why Async

Snip uses FastAPI with async SQLAlchemy and asyncpg. Every database operation is awaited. This was a deliberate choice. In a URL shortener, the redirect path is the hottest code path. It runs on every short link click. If the database query blocks, the entire server stalls for that request. With asyncpg, waiting on the database does not block the event loop, allowing the server to continue handling other requests while that query is in progress.

Keeping everything async also means one session factory, one query style, one way of doing things. No mixing sync and async database patterns in the same application.

## What This Project Taught Me

Snip is a small product with real backend concerns: a public write path with duplicate detection, a redirect path that must stay fast, click tracking, admin-only analytics and deletion, and migration-backed schema changes. The project deliberately avoids user accounts. The goal was a clean, stateless public tool: paste, shorten, share. That architectural focus is what made it a good learning project. The bugs I found were all in the integration layer, the kind you only find by testing the full stack under realistic conditions.

---

*Live at [snip-ly.xyz](https://snip-ly.xyz) · Code at [github.com/RichardOyelowo/Snip](https://github.com/RichardOyelowo/Snip)*
