---
title: "Why I Built gatevault and What Packaging for PyPI Taught Me"
date: "2026-07-21"
summary: "Most auth libraries do one thing. I kept solving the same auth problem across projects, so I packaged the solution."
pinned: true
---

# Why I Built gatevault and What Packaging for PyPI Taught Me

## The Problem I Kept Solving

Every backend project needs auth. Hash a password, verify credentials, create access and refresh tokens, protect routes, decode token payloads, raise useful errors. I was writing these same patterns in every project. FastAPI apps, Flask apps, scripts that needed OAuth2. Each time I would grab PyJWT for encoding, bcrypt for hashing, and then wire the whole flow together myself.

Most Python auth libraries do one thing well. PyJWT gives you JWT encoding. bcrypt gives you password hashing. But you still have to write the login flow, build the guards, handle the exceptions, and repeat that boilerplate across every project. The integration work between these libraries was the real problem, and nobody was solving it in a framework-agnostic way.

After the third time I copy-pasted my auth helpers into a new project, I decided to extract them into a proper package. That package became gatevault.

## What gatevault Actually Does

gatevault is a Python auth library that handles four things in one coherent API:

1. **JWT token management** with access tokens (configurable expiry, default 15 minutes), refresh tokens (default 7 days), and tamper detection built on top of PyJWT
2. **bcrypt password hashing** with automatic salt management so you never accidentally skip salting
3. **OAuth2 login flow** that handles user lookup, credential verification, and token pair generation in one call
4. **Route protection** via a `@gate.protected` decorator that decodes the token, validates it, and injects the payload into any sync or async function

The key design decision was framework-agnosticism. gatevault does not care if you are using FastAPI with async SQLAlchemy, Flask with a sync ORM, Django with its built-in ORM, or just plain Python scripts. There are integration examples for all four in the README, but the core library has zero framework imports.

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

One import, one instance, one decorator. The token payload gets injected directly into your function. No middleware configuration, no request object parsing, no framework-specific dependency injection. If you want the raw tokens, you can call `tm.create_token_pair(user_data)` directly. If you just want hashing, import `Hasher` alone. The library works as a whole or in parts.

## Token Refresh and Rotation

The refresh flow was one of the trickier parts to get right. When a user's access token expires, they send their refresh token to get a new token pair. The question is: what happens to the old refresh token?

The approach I settled on is family-based rotation. Each refresh token belongs to a "family" that traces back to the original login. When you use a refresh token, a new pair is generated and the used token is marked as rotated. If someone tries to reuse an already-rotated refresh token, the entire family is invalidated. This catches token theft: if an attacker replays a stolen refresh token after the legitimate user has already used it, the family gets killed and both the attacker and the legitimate user are forced to re-authenticate.

This caught a subtle bug during testing. The initial implementation could accept an already-rotated refresh token if called twice in quick succession, because the rotation check was not atomic. The fix was to track rotation state in the token's claims and check it on every refresh attempt.

## What I Learned Packaging for PyPI

### pyproject.toml is the Standard Now

I started with `setup.py` out of habit, then moved everything to `pyproject.toml`. It is cleaner. Dependencies, build system, metadata, and entry points all live in one file. If you are starting a new package today, skip `setup.py` entirely. The build backend I used is `setuptools` with `setuptools-scm`, but `hatch` and `flit` are also solid choices.

### Dependency Management is a Design Decision

I made gatevault depend on PyJWT and bcrypt, but nothing else. No Flask, no FastAPI, no httpx. This was deliberate. Adding framework dependencies would have made the package heavier and limited its use cases. Users bring their own framework. gatevault just handles auth primitives.

This meant I had to design the API to work without assuming any framework's request/response model. The `@gate.protected` decorator extracts the token from a keyword argument, not from a framework-specific header parser. The caller is responsible for pulling the token out of the request. This keeps the library honest and framework-free.

### Exception Hierarchy Matters

Early on, gatevault raised raw PyJWT exceptions like `ExpiredSignatureError` and `InvalidTokenError`. These leak implementation details to the caller and make it hard to handle errors consistently. I wrapped all of this into a clean exception hierarchy:

- `GateVaultError` (base)
  - `TokenExpiredError`
  - `TokenInvalidError`
  - `TokenMissingError`
  - `HashingError`
  - `OAuthError`

Now callers can catch `GateVaultError` broadly or handle specific cases. The internal PyJWT exceptions are still raised under the hood, but they never escape the package boundary. This is important for library design: your users should never need to import your dependencies to handle your errors.

### Testing a Library is Different from Testing an App

With an application, you test endpoints and flows. With a library, you test contracts. Every public function has specific input/output behavior that callers depend on. I wrote a full pytest suite covering:

- Token creation and verification, including expiry and tamper detection
- Password hashing and verification with round-trip guarantees
- The protected decorator in both sync and async contexts
- Exception types for every failure mode
- Token refresh rotation and family-based invalidation

The test suite is the most important part of the package. A library with no tests is a liability, not a tool.

### CI and Automated PyPI Releases

I set up GitHub Actions to run the test suite on every push and automatically publish to PyPI when a new version tag is pushed. The workflow is straightforward: push to `main` and tests run. Push a tag like `v0.3.0` and tests run, then `twine upload` publishes to PyPI.

This means I never manually build or upload. I bump the version in `pyproject.toml`, tag the commit, and GitHub does the rest. It eliminates the "did I publish the right version?" anxiety that comes with manual publishing.

## What I Would Do Differently

**Type hints with stub files.** I have inline type hints, but generating `.pyi` stub files would make IDE support better for users. Right now, type checkers can infer types from the source, but dedicated stub files are the standard for library packages.

**Async-first design.** I supported both sync and async from the start, but making it async-first with sync wrappers might have been cleaner architecturally. The current approach works, but the dual-path adds internal complexity.

**More granular token claims.** Right now you pass a dict of claims. A `TokenClaims` dataclass with validation would catch mistakes at creation time instead of at decode time. If you misspell a key in the claims dict, you will not find out until you try to decode the token later.

## The Takeaway

gatevault is not trying to replace Auth0 or a full identity platform. It is for Python apps that need direct, understandable auth primitives without the overhead of a framework-specific solution. If you are building a FastAPI or Flask app and you are tired of wiring JWT, bcrypt, and OAuth2 together for the fifth time, it might save you an afternoon.

---

*If you want to dig into the code or see framework integration examples, the full README covers FastAPI (async and sync), Flask, Django, and Django REST Framework: [github.com/RichardOyelowo/gatevault](https://github.com/RichardOyelowo/gatevault)*
