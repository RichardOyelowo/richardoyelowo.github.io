---
title: "Collision-Safe URL Shortening with an Atomic Base62 Counter"
date: "2025-07"
summary: "How Snip generates unique short codes without UUIDs, retry loops, or database-level locking. The 4 bugs caught before launch."
pinned: false
---

# Collision-Safe URL Shortening with an Atomic Base62 Counter

## Why Not Just Use UUIDs?

Most URL shorteners either generate random short codes and check for collisions with retry loops, or use UUIDs truncated to some length. Both approaches have problems.

Random generation with retry loops means you are doing extra database queries on every write. Under concurrent load, two requests can both check "is this code taken?", both get "no", and both try to insert, causing one to fail and retry. This is a classic TOCTOU (time-of-check-time-of-use) race condition. The retry loop masks the problem but does not fix it. Under high concurrency, the retry count increases and the latency spikes.

UUIDs avoid collisions by being statistically unique (122 bits of randomness), but they are long. A UUIDv4 is 36 characters. Even truncated to 7 characters, you lose the collision guarantee and the output is base36-ish which wastes characters. You also cannot sort UUIDs chronologically, which makes analytics harder. If you want to know "which short link was created first?", you cannot tell from the code alone.

I wanted something different for Snip: every short code guaranteed unique by construction, with zero retry loops and zero collision checks.

## The Approach: Atomic Counter + Base62

The idea is simple. Use an auto-incrementing integer as the primary key, then encode that integer in Base62. The result is your short code.

Base62 uses the characters `0-9a-zA-Z`, giving 62 possible values per character. A 7-character Base62 string can represent 62 to the power of 7, which is roughly 3.5 trillion unique values. The first URL gets code `0000001`, the second `0000002`, and so on. Since the counter only goes up, collisions are impossible by definition.

The critical part is the counter. In PostgreSQL, a `SERIAL` or `IDENTITY` column with a `UNIQUE` constraint gives you this guarantee at the database level. The sequence generator handles concurrency. PostgreSQL assigns the next value atomically, so two concurrent inserts always get different IDs. No application-level locking needed, no retry loops, no optimistic concurrency control.

```python
import string

BASE62 = string.digits + string.ascii_letters

def encode_base62(num: int) -> str:
    if num == 0:
        return BASE62[0]
    encoded = []
    while num > 0:
        num, rem = divmod(num, 62)
        encoded.append(BASE62[rem])
    return ''.join(reversed(encoded))
```

A counter value of `123456789` encodes to `8M0kX`, which is 5 characters for 123 million URLs. At this scale, you are not wasting characters and you are not risking collisions. The encoding function is pure math, no state, no side effects, no database calls. It runs in O(log n) time where n is the counter value.

## Duplicate URL Detection

Snip has a second requirement: if you paste the same URL twice, you get the same short link back. This is not just a convenience feature. It means the database does not grow with duplicate entries, and analytics are accurate (all clicks on the same long URL are tracked under one short code).

The implementation checks for an existing `original_url` before creating a new entry. If a match is found, the existing short code is returned. This check uses a `UNIQUE` constraint on `original_url` combined with proper conflict handling in the async session.

## The Bug That Only Appeared Under Concurrent Load

This is where the integration tests proved their worth. I wrote a test suite that simulates concurrent URL shortening requests using `asyncio.gather` with multiple simultaneous calls to the create endpoint. Without this test, everything passed in normal sequential testing.

The bug was in how I was reading the counter back after insert. In async SQLAlchemy, if you do not `await` the session flush before reading the auto-generated ID, you can get `None` back or worse, a stale cached value. Under sequential testing this almost never triggers because the session has time to sync. Under concurrent load with connection pooling, it triggers regularly.

The fix was explicitly flushing the session after insert and before encoding:

```python
async with session.begin():
    link = URL(original_url=url)
    session.add(link)
    await session.flush()  # forces DB to assign the ID
    link.short_code = encode_base62(link.id)
```

The `await session.flush()` call is the key line. It sends the INSERT to PostgreSQL and gets back the auto-generated ID, all without committing the transaction. The short code is then computed from that ID and set on the model before the transaction commits. This guarantees the short code is always correct, even under concurrent load.

## Three More Bugs From the Same Test Suite

The concurrent load test caught four bugs total. The counter race condition was the most subtle, but the other three were also real problems that would have hit production.

**Missing commit in click tracking.** The click logger was inserting `Click` records but the async context manager was not committing properly, so clicks were lost on restart. The fix was ensuring the click insertion used a proper `async with session.begin()` block. Every write operation in Snip now uses this pattern consistently.

**Duplicate URL detection race.** Two requests for the same URL could both pass the "does this URL exist?" check and both create new entries. Fixed with a `UNIQUE` constraint on `original_url` and proper conflict handling that catches the `IntegrityError` and falls back to returning the existing entry.

**Redirect lookup not using parameterized queries.** The redirect endpoint was vulnerable to timing-based information leakage because it was not using parameterized queries consistently. Fixed by switching all queries to `text()` with bound parameters. This is a basic security fix that every database-backed application needs, but it is easy to miss when you are writing queries in multiple places.

## Why Async All the Way

Snip uses async SQLAlchemy with asyncpg as the PostgreSQL driver. Every database operation is awaited. This was a deliberate choice, not just for performance.

With a sync driver like `psycopg2`, every database call blocks the event loop. In a URL shortener, the redirect path is the hottest code path. It runs on every short link click. If the database query blocks, the entire server stalls for that request. With asyncpg, the query yields control back to the event loop while waiting for the database response, so other requests can be served in parallel.

The admin dashboard and analytics endpoints benefit less from async since they are low-traffic, but keeping everything async means I do not need to manage two different database session patterns in the same application. One session factory, one query style, one way of doing things.

## Click Tracking Without Blocking Redirects

Every redirect logs a `Click` record with a timestamp, referrer, and the short code. The challenge: this logging must not slow down the redirect.

The solution was to make click logging fire-and-forget within the same request context. The redirect response is prepared first, the click is logged, and the response is returned. Since both operations hit the same database and the logging is a simple INSERT, the overhead is minimal. But if it ever becomes a bottleneck, the architecture supports moving it to a background task or a message queue without changing the redirect logic.

The Click model stores the short code, the timestamp, and the referrer (if provided by the browser). This data powers the admin analytics page, which shows click counts per short link and referrer breakdowns. It is simple but useful for understanding how short links are being used.

## Deployment and Docker

Snip runs on Railway with a PostgreSQL database. The deployment is containerized with Docker, which means the production environment is identical to the development environment. The Dockerfile uses a multi-stage build: the first stage installs dependencies and the second stage copies only what is needed to run the app.

The CI pipeline runs on every push to GitHub. It runs the full test suite (including the concurrent load tests) and builds the Docker image. If the tests pass and the branch is `main`, the image is pushed to Railway. The entire pipeline is automated. I push code, tests run, and if they pass, the app deploys. No manual steps.

## What This Project Taught Me

Snip is a small product with real backend concerns:

- **Public write path with duplicate detection**, where the same URL always returns the same short link
- **Redirect path that must stay fast**, because every millisecond matters on the hot path
- **Click tracking without blocking**, so analytics cannot degrade the core product
- **Admin-only analytics and deletion**, with proper auth guards
- **Migration-backed schema changes**, where Alembic handles all database evolution
- **Integration tests around real user flows**, not unit tests in isolation, but full request-response cycles that caught 4 production bugs

The project deliberately avoids user accounts. The goal was a clean, stateless public tool: paste, shorten, share. And to focus on the backend architecture rather than auth complexity. That architectural focus is what made it a good learning project. The bugs I found were all in the integration layer, the kind you only find by testing the full stack under realistic conditions.

---

*Live at [snip-ly.xyz](https://snip-ly.xyz) · Code at [github.com/RichardOyelowo/Snip](https://github.com/RichardOyelowo/Snip)*