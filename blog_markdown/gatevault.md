---
title: "Why I Built gatevault and What Packaging for PyPI Taught Me"
date: "2026-07-21"
summary: "Most auth libraries do one thing. I kept solving the same auth problem across projects, so I packaged the solution."
pinned: true
---

# Why I Built gatevault and What Packaging for PyPI Taught Me

## The Problem I Kept Solving

Every backend project needs auth. Hash a password, verify credentials, create access and refresh tokens, protect routes, decode token payloads, raise useful errors. I was writing these same patterns in every project. FastAPI apps, Flask apps, scripts that needed OAuth2. Each time I would grab PyJWT for encoding, bcrypt for hashing, and then wire the whole flow together myself.

Most Python auth libraries do one thing well. PyJWT gives you JWT encoding. bcrypt gives you password hashing. But you still have to write the login flow, build the guards, handle the exceptions, and repeat that boilerplate across every project. The integration work between these libraries was the real problem, and I could not find a framework-agnostic solution that matched how I wanted to build applications.

After the third time I copy-pasted my auth helpers into a new project, I decided to extract them into a proper package. That package became gatevault.

## What gatevault Actually Does

gatevault is a Python auth library that handles four things in one coherent API:

1. **JWT token management** with access tokens (configurable expiry, default 15 minutes), refresh tokens (default 7 days), and tamper detection built on top of PyJWT
2. **bcrypt password hashing** with automatic salt management so you never accidentally skip salting
3. **OAuth2-compatible bearer token authentication flow** that handles user lookup, credential verification, and token pair generation in one call
4. **Route protection** via a `@gate.protected` decorator that decodes the token, validates it, and injects the payload into any sync or async function

The key design decision was framework-agnosticism. gatevault does not care if you are using FastAPI with async SQLAlchemy, Flask with a sync ORM, Django with its built-in ORM, or just plain Python scripts. There are integration examples for all four in the README, but the core library has zero framework imports.

## Design Decisions

gatevault intentionally focuses on authentication primitives rather than application-specific identity management.

It handles:
- password hashing
- token generation
- token validation
- authentication guards

It does not manage:
- users
- databases
- sessions
- authorization rules
- refresh token persistence

Keeping these concerns separate allows applications to adopt gatevault without being forced into a specific data model or architecture.

## The API I Wanted

Here is what I was aiming for:

```python
from gatevault import TokenManager, GateVault

tm = TokenManager(
    secret_key=SECRET,
    access_expiry_minutes=15,
    refresh_expiry_days=7
)

gate = GateVault(token_manager=tm)

@gate.protected
def get_profile(payload=None):
    return db.get_user(payload["user_id"])
```

One import, one instance, one decorator. The token payload gets injected directly into your function. No middleware configuration, no request object parsing, no framework-specific dependency injection. If you want the raw tokens, you can call `tm.create_access_token(user_id)` and `tm.create_refresh_token(user_id)` directly. If you just want hashing, import `hash_password` and `verify_password` as standalone functions. The library works as a whole or in parts.

User IDs are flexible. The library supports `int`, `str`, and `UUID` out of the box through a `normalize_user_id` function that runs before encoding. If your app uses something custom, you can pass your own encoder callable to `TokenManager`:

```python
from uuid import UUID
from gatevault import TokenManager

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

tm.create_access_token(user_id=1)                    # int
tm.create_access_token(user_id="user-abc-123")       # str
tm.create_access_token(user_id=UUID("a1b2c3d4-...")) # UUID, serialized to str automatically
```

Extra claims go through `**kwargs` and end up in the decoded payload. This lets you attach role, org, or permission data without any extra configuration:

```python
access = tm.create_access_token(user_id=42, role="admin", org_id="richard-corp")

payload = tm.decode_token(access)
print(payload["role"])     # admin
print(payload["org_id"])  # richard-corp
```

The standalone hashing API is intentionally simple. Two functions, no classes to instantiate:

```python
from gatevault import hash_password, verify_password

hashed = hash_password("user_password_123")
# $2b$12$examplebcrypthash...

is_valid = verify_password("user_password_123", hashed)   # True
is_valid = verify_password("wrong_password", hashed)       # False
```

Salt is generated and embedded automatically by bcrypt. You store the full hash string in your database. When verifying, bcrypt extracts the salt from the stored hash and uses it to re-hash the input. There is no separate salt column and no way to accidentally hash without one. If anything goes wrong internally, it raises a `HashingError` instead of a raw bcrypt exception.

For the full login flow, `OAuthHandler` wires together user lookup, password verification, and token generation in one call. You provide a `get_user` callable that returns an object with `id` and `hashed_password` attributes:

```python
from gatevault import TokenManager, OAuthHandler

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

# Sync user lookup
def get_user_from_db(username: str):
    return db.execute("SELECT id, hashed_password FROM users WHERE email = ?", (username,)).fetchone()

handler = OAuthHandler(token_manager=tm, get_user=get_user_from_db)
tokens = handler.login("john@example.com", "mypassword")
# {"access_token": "eyJ...", "refresh_token": "eyJ...", "token_type": "bearer"}
```

For async codebases, use `async_login` with an async `get_user`:

```python
async def get_user(username: str):
    result = await db.execute(select(User).where(User.email == username))
    return result.scalar_one_or_none()

handler = OAuthHandler(token_manager=tm, get_user=get_user)
tokens = await handler.async_login("john@example.com", "mypassword")
```

Three distinct exceptions cover every login failure. `InvalidCredentialsError` when no user is found, `UnauthorizedError` when the password does not match, `GuardError` when token creation fails (for example, if the user ID is an unsupported type).

The decorator works with both sync and async functions. It inspects the function and returns the appropriate wrapper automatically, so you do not have to think about it:

```python
from gatevault import TokenManager, GateVault

gate = GateVault(token_manager=tm)

# Sync
@gate.protected
def get_orders(payload=None):
    return db.get_orders(payload["user_id"])

# Async
@gate.protected
async def get_orders_async(payload=None):
    return await db.fetch_orders(payload["user_id"])

# Call either by passing the token kwarg
get_orders(token="eyJhbGci...")
```

The decorator extracts the token from the `token` keyword argument. This is deliberate. gatevault does not parse framework-specific request objects. The caller pulls the token out of the `Authorization` header or cookie in their framework, then passes it in. This keeps the library honest and framework-free.

## Token Refresh and Rotation

The refresh flow was one of the trickier parts to get right. When a user's access token expires, they send their refresh token to get a new token pair. The question is: what happens to the old refresh token?

A common approach for implementing refresh token rotation is family-based rotation. Each refresh token belongs to a "family" that traces back to the original login. When a refresh token is used, the application generates a new pair and marks the used token as rotated. If someone tries to reuse an already-rotated refresh token, the entire family is invalidated. This catches token theft: if an attacker replays a stolen refresh token after the legitimate user has already used it, the family gets killed and both the attacker and the legitimate user are forced to re-authenticate.

During development, this exposed a subtle concurrency issue. An early implementation could accept an already-rotated refresh token if called twice in quick succession because the rotation check was not atomic. The fix was to make the rotation check atomic. Refresh token state cannot rely only on the JWT payload because the token itself is immutable after issuance. The application must maintain external state for used tokens or token families, ensuring that concurrent refresh attempts cannot both succeed.

Access tokens are intentionally short-lived. Refresh tokens allow applications to obtain new access tokens without requiring users to authenticate again.

JWT access tokens are stateless by design, meaning they cannot be individually revoked after issuance without external state.

gatevault therefore provides the primitives required for refresh token workflows but leaves persistence, rotation, and revocation decisions to the consuming application. The `TokenManager` gives you token creation and decoding. The refresh endpoint itself is application-level code:

```python
from gatevault import TokenManager, TokenExpiredError, InvalidTokenError

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

def refresh_tokens(refresh_token: str) -> dict:
    payload = tm.decode_token(refresh_token)

    if payload["type"] != "refresh":
        raise ValueError("Expected refresh token")

    # Your app handles rotation/revocation here
    # e.g. check if this token has been rotated already

    new_access = tm.create_access_token(user_id=payload["user_id"])
    new_refresh = tm.create_refresh_token(user_id=payload["user_id"])

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer"
    }
```

Applications that require token rotation or immediate invalidation can store refresh token identifiers externally and enforce their own lifecycle policies.

## What I Learned Packaging for PyPI

### pyproject.toml is the Standard Now

I started with `setup.py` out of habit, then moved everything to `pyproject.toml` with `hatchling` as the build backend. It is cleaner. Dependencies, build system, metadata, and entry points all live in one file. If you are starting a new package today, skip `setup.py` entirely.

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/gatevault"]

[project]
name = "richard-gatevault"
version = "1.0.6"
requires-python = ">=3.9"
dependencies = ["PyJWT", "bcrypt"]
```

The `src` layout with `packages = ["src/gatevault"]` prevents accidental imports of the local directory instead of the installed package during development. Python finds packages on the path, and without the `src/` directory, the local uninstalled code can shadow the pip-installed version. Small detail, saves debugging time.

### Dependency Management is a Design Decision

I made gatevault depend on PyJWT and bcrypt, but nothing else. No Flask, no FastAPI, no httpx. This was deliberate. Adding framework dependencies would have made the package heavier and limited its use cases. Users bring their own framework. gatevault just handles auth primitives. If framework-specific conveniences are added in the future, they should live in optional integrations rather than the core package.

This meant I had to design the API to work without assuming any framework's request/response model. The `OAuthHandler` accepts a plain `get_user` callable instead of a database connection. The `@gate.protected` decorator extracts the token from a keyword argument, not from a framework-specific header parser. The caller is responsible for pulling the token out of the request. This keeps the library honest and framework-free.

If the secret key is shorter than 32 bytes, gatevault does not block you but issues a `ShortKeyWarning`. This is intentional. During development you might use a short key for convenience, but you should always use at least 32 bytes in production. HS256 with a short secret is not secure.

### Exception Hierarchy Matters

Early on, gatevault raised raw PyJWT exceptions like `ExpiredSignatureError` and `InvalidTokenError`. These leak implementation details to the caller and make it hard to handle errors consistently. I wrapped all of this into a clean exception hierarchy:

- `GatevaultError` (base)
  - `TokenError` (token base)
    - `TokenExpiredError`
    - `InvalidTokenError`
    - `TokenDecodeError`
  - `HashingError`
  - `GuardError` (guard base)
    - `InvalidCredentialsError`
    - `UnauthorizedError`

Now callers can catch `GatevaultError` broadly or handle specific cases. The internal PyJWT exceptions are still raised under the hood, but they never escape the package boundary. This is important for library design: your users should never need to import your dependencies to handle your errors.

```python
from gatevault import (
    GatevaultError, TokenExpiredError,
    InvalidTokenError, GuardError,
    HashingError, InvalidCredentialsError,
    UnauthorizedError
)

# Broad catch-all
try:
    payload = tm.decode_token(token)
except GatevaultError as e:
    return {"error": str(e)}

# Granular handling for login failures
try:
    tokens = handler.login(email, password)
except InvalidCredentialsError:
    return {"error": "No account found"}
except UnauthorizedError:
    return {"error": "Wrong password"}
except GuardError as e:
    return {"error": f"Auth error: {e}"}
```

### Testing a Library is Different from Testing an App

With an application, you test endpoints and flows. With a library, you test contracts. Every public function has specific input/output behavior that callers depend on. I wrote a full pytest suite covering:

- Token creation and verification, including expiry and tamper detection
- Password hashing and verification with round-trip guarantees
- The protected decorator in both sync and async contexts
- Exception types for every failure mode
- Token refresh rotation and family-based invalidation

The test suite is the most important part of the package. A library with no tests is a liability, not a tool.

### CI and Automated PyPI Releases

I set up GitHub Actions to run the test suite on every push and automatically publish to PyPI when a new version tag is pushed. The workflow is straightforward: push to `main` and tests run. Push a tag like `v1.0.6` and tests run, then the package publishes to PyPI.

This means I never manually build or upload. I bump the version in `pyproject.toml`, tag the commit, and GitHub does the rest. It eliminates the "did I publish the right version?" anxiety that comes with manual publishing.

## What I Would Do Differently

**Type hints with stub files.** I have inline type hints, but generating `.pyi` stub files would make IDE support better for users. Right now, type checkers can infer types from the source, but dedicated stub files are the standard for library packages.

**Async-first design.** I supported both sync and async from the start. While the cryptographic operations themselves are CPU-bound and naturally synchronous, providing async-compatible interfaces earlier may have reduced friction for async-first frameworks. Making the library async-first with synchronous wrappers might have been cleaner architecturally, but the current approach works well. The dual-path does introduce additional internal complexity.

**More granular token claims.** Right now you pass additional claims as `**kwargs` to the token creation methods. A `TokenClaims` dataclass with validation would catch mistakes at creation time instead of at decode time. If you misspell a key in the claims dict, you will not find out until you try to decode the token later.

## The Takeaway

gatevault is not trying to replace Auth0 or a full identity platform. It is for Python apps that need direct, understandable auth primitives without the overhead of a framework-specific solution. If you are building a FastAPI or Flask app and you are tired of wiring JWT, bcrypt, and OAuth2 together for the fifth time, it might save you an afternoon.

---

*If you want to dig into the code or see framework integration examples, the full README covers FastAPI (async and sync), Flask, Django, and Django REST Framework: [github.com/RichardOyelowo/gatevault](https://github.com/RichardOyelowo/gatevault)*
