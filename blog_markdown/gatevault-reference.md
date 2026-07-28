---
title: "How gatevault Handles Authentication in Python"
date: "2026-07-27"
summary: "Every public function, class, and exception in gatevault, with code examples showing password hashing, JWT token management, OAuth2 login, and route protection."
pinned: true
---

# How gatevault Handles Authentication in Python

## what gatevault is

gatevault is a Python authentication library that provides JWT token management, bcrypt password hashing, OAuth2 password credentials flow, and route protection. It has zero framework dependencies. The only runtime requirements are PyJWT and bcrypt.

Install:

```
pip install richard-gatevault
```

Every public export:

```python
from gatevault import (
    GateVault,
    OAuthHandler,
    TokenManager,
    UserID,
    normalize_user_id,
    hash_password,
    verify_password,
    ShortKeyWarning,
    GatevaultError,
    TokenError,
    TokenExpiredError,
    InvalidTokenError,
    TokenDecodeError,
    HashingError,
    GuardError,
    InvalidCredentialsError,
    UnauthorizedError,
)
```

---

## Password Hashing

Two standalone functions. No class instantiation needed. No configuration. No salt management. bcrypt handles everything.

### hash_password(plain: str) -> str

Takes a plain text string, hashes it with bcrypt, returns the full hash string including the embedded salt.

```python
from gatevault import hash_password

hashed = hash_password("my_secure_password")
# $2b$12$N9qo8...
```

The salt is generated automatically by bcrypt and embedded in the hash string. You store this full string in your database. There is no separate salt column. There is no way to hash without a salt.

If bcrypt fails internally, it raises a `HashingError`. This wraps the raw bcrypt exception so callers never need to import bcrypt to handle errors.

### verify_password(plain: str, hashed: str) -> bool

Takes a plain text password and a stored bcrypt hash. Returns `True` if they match, `False` if they don't. Does not raise on mismatch.

```python
from gatevault import verify_password

# Correct password
verify_password("my_secure_password", "$2b$12$N9qo8...")  # True

# Wrong password
verify_password("wrong_password", "$2b$12$N9qo8...")     # False
```

bcrypt extracts the salt from the stored hash string and uses it to re-hash the input. That is why you only need the hash, not a separate salt.

### Practical usage

```python
from gatevault import hash_password, verify_password, HashingError

# During registration
def register(db, email: str, password: str):
    if db.find_user_by_email(email):
        raise ValueError("Email already registered")

    try:
        hashed = hash_password(password)
    except HashingError:
        raise ValueError("Registration failed")

    user = db.create_user(email=email, password_hash=hashed)
    return user

# During login
def login(db, email: str, password: str):
    user = db.find_user_by_email(email)
    if not user:
        raise ValueError("Invalid credentials")

    if not verify_password(password, user.password_hash):
        raise ValueError("Invalid credentials")

    return user
```

---

## Token Management

### TokenManager

The central class for JWT token creation and verification. Tokens are signed with HS256.

```python
from gatevault import TokenManager

tm = TokenManager(
    secret_key="your-very-secure-secret-key-here",
    access_expiry_minutes=15,
    refresh_expiry_days=7
)
```

**Constructor parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `secret_key` | `str` | Yes | Secret key for signing tokens. Should be at least 32 bytes. |
| `access_expiry_minutes` | `int` | Yes | How long access tokens live. Typical: 5-60 minutes. |
| `refresh_expiry_days` | `int` | Yes | How long refresh tokens live. Typical: 1-30 days. |
| `user_id_encoder` | `Callable[[object], Union[int, str]]` | No | Custom encoder for user IDs. Defaults to `normalize_user_id`. |

If `secret_key` is shorter than 32 bytes, a `ShortKeyWarning` is emitted. The library does not block you, but you should use a longer key in production. HS256 with a short secret is not secure.

### User ID types

gatevault supports `int`, `str`, and `UUID` out of the box. The `normalize_user_id` function converts these into an encodable form before the token is created. UUIDs are serialized to strings automatically.

```python
from uuid import UUID
from gatevault import TokenManager

tm = TokenManager(
    secret_key="your-very-secure-secret-key-here",
    access_expiry_minutes=15,
    refresh_expiry_days=7
)

# All three work without any configuration
tm.create_access_token(user_id=1)                           # int
tm.create_access_token(user_id="user-abc-123")              # str
tm.create_access_token(user_id=UUID("a1b2c3d4-e5f6-...")) # UUID -> str
```

If your app uses a custom type for user IDs, pass a custom encoder:

```python
def my_encoder(user_id):
    # Convert your custom type to int or str
    return str(user_id.mongo_id)

tm = TokenManager(
    secret_key=SECRET,
    access_expiry_minutes=15,
    refresh_expiry_days=7,
    user_id_encoder=my_encoder
)
```

The encoder must return an `int` or `str`. If it returns something else, `TypeError` is raised at token creation time.

### create_access_token(user_id, **kwargs) -> str

Creates a short-lived JWT access token.

```python
from gatevault import TokenManager

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

# Minimal
token = tm.create_access_token(user_id=42)
# eyJhbGciOiJIUzI1NiIs...

# With extra claims
token = tm.create_access_token(user_id=42, role="admin", org="acme-corp")
```

The token payload contains:

- `user_id`: the encoded user ID
- `exp`: expiry timestamp (UTC)
- `type`: always `"access"`
- `jti`: a random UUID generated at signing time, unique to this token
- Any additional claims passed as `**kwargs`

### create_refresh_token(user_id, **kwargs) -> str

Creates a long-lived JWT refresh token. Same interface as `create_access_token` but the `type` claim is `"refresh"` and the expiry uses `refresh_expiry_days`.

```python
access = tm.create_access_token(user_id=42, role="admin")
refresh = tm.create_refresh_token(user_id=42)
```

You can attach different claims to each token type. A common pattern is minimal claims on the refresh token and rich claims on the access token.

### decode_token(token: str) -> dict

Decodes and verifies a JWT token. Checks the signature and expiry. Returns the decoded payload dictionary.

```python
from gatevault import TokenManager, TokenExpiredError, InvalidTokenError, TokenDecodeError

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

token = tm.create_access_token(user_id=42, role="admin")

payload = tm.decode_token(token)
print(payload["user_id"])  # 42
print(payload["role"])     # admin
print(payload["type"])     # access
print(payload["exp"])      # 1751347200 (unix timestamp)
print(payload["jti"])      # 3fa85f64-5717-4562-b3fc-2c963f66afa6
```

`jti` is set automatically on every token, you don't pass it in. It's what keeps two tokens issued in the same second from being byte-identical, since JWT signing is deterministic and without it nothing in the payload would vary at that resolution.

**Exceptions raised:**

| Exception | When |
|---|---|
| `TokenExpiredError` | The token has expired |
| `InvalidTokenError` | The token signature is invalid |
| `TokenDecodeError` | The token is malformed or cannot be decoded |

These are gatevault's own exceptions. The raw PyJWT exceptions (`ExpiredSignatureError`, `InvalidSignatureError`, `DecodeError`) are caught internally and re-raised as the appropriate gatevault exception. Callers never need to import PyJWT to handle errors.

---

## Route Protection

### GateVault

Wraps a `TokenManager` into a decorator factory. Any function decorated with `@gate.protected` will not execute without a valid token. The decoded payload is injected as a `payload` keyword argument.

```python
from gatevault import TokenManager, GateVault

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)
gate = GateVault(token_manager=tm)

@gate.protected
def get_profile(payload=None):
    user_id = payload["user_id"]
    return db.get_user(user_id)
```

### How the decorator works

1. The caller passes the token string as the `token` keyword argument
2. The decorator calls `tm.decode_token(token)`
3. On success, the decoded payload is injected into the wrapped function as `payload`
4. On failure, an exception is raised before the function executes

The decorator inspects the wrapped function. If it is an async function (`inspect.iscoroutinefunction`), it returns an async wrapper. If it is sync, it returns a sync wrapper. You do not need to think about this.

### Sync usage

```python
@gate.protected
def get_orders(payload=None):
    return db.get_orders(payload["user_id"])

# Call it
get_orders(token="eyJhbGci...")
```

### Async usage

```python
@gate.protected
async def get_orders(payload=None):
    return await db.fetch_orders(payload["user_id"])

# Call it
await get_orders(token="eyJhbGci...")
```

### Exceptions from the decorator

| Exception | When |
|---|---|
| `GuardError("No token provided")` | `token` is `None` or falsy |
| `GuardError("Unable to decode token")` | Token is malformed (`TokenDecodeError`) |
| `GuardError("Token has expired")` | Token has expired (`TokenExpiredError`) |
| `UnauthorizedError("Invalid token")` | Token signature is invalid (`InvalidTokenError`) |

### FastAPI integration example

gatevault does not parse framework-specific request objects. The caller extracts the token from the request and passes it in. This keeps the library framework-free.

```python
from fastapi import FastAPI, Header
from fastapi.responses import JSONResponse
from gatevault import TokenManager, GateVault, GuardError, UnauthorizedError

app = FastAPI()
tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)
gate = GateVault(token_manager=tm)

def get_profile(payload=None):
    user = db.get_user(payload["user_id"])
    return {"id": user.id, "email": user.email}

@app.get("/me")
def me(authorization: str = Header(None)):
    token = authorization.split(" ")[1] if authorization else None

    try:
        return gate.protected(get_profile)(token=token)
    except GuardError as e:
        return JSONResponse(status_code=401, content={"error": str(e)})
    except UnauthorizedError:
        return JSONResponse(status_code=401, content={"error": "Invalid token"})
```

### Flask integration example

```python
from flask import Flask, request, jsonify
from gatevault import TokenManager, GateVault, GuardError, UnauthorizedError

app = Flask(__name__)
tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)
gate = GateVault(token_manager=tm)

def get_profile(payload=None):
    user = db.get_user(payload["user_id"])
    return {"id": user.id, "email": user.email}

@app.route("/me")
def me():
    auth = request.headers.get("Authorization", "")
    token = auth.split(" ")[1] if auth.startswith("Bearer ") else None

    try:
        return jsonify(gate.protected(get_profile)(token=token))
    except GuardError as e:
        return jsonify({"error": str(e)}), 401
    except UnauthorizedError:
        return jsonify({"error": "Invalid token"}), 401
```

---

## OAuth2 Password Credentials Flow

### OAuthHandler

Wires together user lookup, password verification, and token generation into a single call. Follows the OAuth2 Resource Owner Password Credentials flow.

```python
from gatevault import TokenManager, OAuthHandler

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)
handler = OAuthHandler(token_manager=tm, get_user=get_user_from_db)
```

**Constructor parameters:**

| Parameter | Type | Required | Description |
|---|---|---|---|
| `token_manager` | `TokenManager` | Yes | A configured TokenManager instance |
| `get_user` | `Callable` | Yes | A callable that accepts a username string and returns an object with `id` and `hashed_password` attributes, or `None` if not found |
| `get_user_by_id` | `Callable` | No | A callable that accepts a user ID and returns the matching user, or `None`. Used only by `async_refresh` to confirm the user still exists before handing out a new token pair. Leave it out and `async_refresh` trusts the `user_id` already signed into the token. |

The `get_user` callable is your integration point. gatevault does not import any ORM or database library. You provide the lookup logic, and gatevault handles password verification and token generation.

### login(username: str, password: str) -> dict

Authenticates a user synchronously and returns a token pair.

```python
from gatevault import TokenManager, OAuthHandler

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

def get_user_from_db(username: str):
    return db.execute(
        "SELECT id, hashed_password FROM users WHERE email = ?", (username,)
    ).fetchone()

handler = OAuthHandler(token_manager=tm, get_user=get_user_from_db)
tokens = handler.login("john@example.com", "mypassword")

print(tokens)
# {
#     "access_token": "eyJhbGciOiJIUzI1NiIs...",
#     "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
#     "token_type": "bearer"
# }
```

### async_login(username: str, password: str) -> dict

Same as `login` but awaits `get_user`. Use this when your lookup function is async.

```python
async def get_user(username: str):
    result = await db.execute(select(User).where(User.email == username))
    return result.scalar_one_or_none()

handler = OAuthHandler(token_manager=tm, get_user=get_user)
tokens = await handler.async_login("john@example.com", "mypassword")
```

### async_refresh(refresh_token: str) -> dict

Exchanges a valid refresh token for a new access and refresh token pair. Rotates on every call, the refresh token you pass in is never handed back out again.

```python
async def get_user_by_id(user_id):
    result = await db.execute(select(User).where(User.id == user_id))
    return result.scalar_one_or_none()

handler = OAuthHandler(token_manager=tm, get_user=get_user, get_user_by_id=get_user_by_id)

tokens = await handler.async_refresh(refresh_token)

print(tokens)
# {
#     "access_token": "eyJhbGci...",
#     "refresh_token": "eyJhbGci...",
#     "token_type": "bearer"
# }
```

Steps, in order:

1. Decode the token. Raises `TokenExpiredError`, `TokenDecodeError`, or `InvalidTokenError` if it's expired, malformed, or tampered with
2. Check the `type` claim. Raises `InvalidTokenError` if it isn't `"refresh"`, an access token can never be used here
3. If `get_user_by_id` was passed at setup, look the user up and raise `InvalidCredentialsError` if they no longer exist
4. Issue a fresh access token and a fresh refresh token

```python
from gatevault import TokenExpiredError, TokenDecodeError, InvalidTokenError, InvalidCredentialsError

try:
    tokens = await handler.async_refresh(refresh_token)
except (TokenExpiredError, TokenDecodeError, InvalidTokenError, InvalidCredentialsError):
    return {"error": "Invalid or expired refresh token"}, 401
```

Collapse all four exceptions to the same generic message and status code. Telling the caller which one it was, expired versus malformed versus a deleted user, hands an attacker probing refresh tokens a way to tell those cases apart.

`async_refresh` rotates the pair but does not track tokens anywhere. It has no way to know if a refresh token was stolen and is being replayed after the legitimate user already rotated past it. If you need that, see [Refresh with rotation](#refresh-with-rotation) below.

### Login flow internals

Both `login` and `async_login` follow the same steps:

1. Call `get_user(username)` to look up the user
2. If no user is found, raise `InvalidCredentialsError("no user found")`
3. Call `verify_password(password, user.hashed_password)`
4. If the password does not match, raise `UnauthorizedError("user password mismatched")`
5. Create an access token and a refresh token from `user.id`
6. Return `{"access_token": ..., "refresh_token": ..., "token_type": "bearer"}`
7. If token creation fails (e.g. unsupported user ID type), raise `GuardError("invalid user id or token_manager error")`

### Login error handling

```python
from gatevault import (
    OAuthHandler, InvalidCredentialsError,
    UnauthorizedError, GuardError
)

handler = OAuthHandler(token_manager=tm, get_user=get_user_from_db)

try:
    tokens = handler.login(email, password)
except InvalidCredentialsError:
    return {"error": "No account found with that email"}, 404
except UnauthorizedError:
    return {"error": "Wrong password"}, 401
except GuardError as e:
    return {"error": f"Auth system error: {e}"}, 500
```

Three distinct exceptions for three distinct failures. No ambiguity.

---

## Token Refresh

`OAuthHandler.async_refresh` (covered above) handles the core exchange, decode, type check, rotate. What it does not do is track tokens anywhere, so persistence and reuse detection are still left to the consuming application. This is deliberate.

JWT tokens are stateless by design. Once issued, they cannot be individually revoked without external state. gatevault does not try to solve that part. It gives you a safe rotation primitive, plus the lower-level `create_access_token`, `create_refresh_token`, and `decode_token` if you want to build the exchange yourself. Your application handles persistence and revocation either way.

### Basic refresh endpoint

If you're not using `OAuthHandler`, or want to see what `async_refresh` is doing internally, here's the same exchange written directly against `TokenManager`:

```python
from gatevault import TokenManager, TokenExpiredError, InvalidTokenError

tm = TokenManager(secret_key=SECRET, access_expiry_minutes=15, refresh_expiry_days=7)

def refresh_tokens(refresh_token: str) -> dict:
    # Decode the refresh token
    payload = tm.decode_token(refresh_token)

    # Verify it is actually a refresh token
    if payload["type"] != "refresh":
        raise ValueError("Expected refresh token")

    # Issue a new pair
    new_access = tm.create_access_token(user_id=payload["user_id"])
    new_refresh = tm.create_refresh_token(user_id=payload["user_id"])

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer"
    }
```

### Refresh with rotation

If you need family-based rotation (where reusing an old refresh token invalidates the entire token family), you would add external state tracking:

```python
def refresh_tokens_with_rotation(refresh_token: str, db) -> dict:
    payload = tm.decode_token(refresh_token)

    if payload["type"] != "refresh":
        raise ValueError("Expected refresh token")

    # Check if this token has been rotated
    token_record = db.get_refresh_token(payload["jti"])
    if token_record and token_record.rotated:
        # Reuse detected. Invalidate the entire family.
        db.invalidate_family(token_record.family_id)
        raise ValueError("Token reuse detected. Please re-authenticate.")

    # Mark current token as rotated
    db.mark_rotated(payload["jti"])

    # Issue new pair in the same family
    new_access = tm.create_access_token(user_id=payload["user_id"])
    new_refresh = tm.create_refresh_token(user_id=payload["user_id"])

    # Store new token with the same family ID
    db.store_refresh_token(new_refresh, family_id=token_record.family_id)

    return {
        "access_token": new_access,
        "refresh_token": new_refresh,
        "token_type": "bearer"
    }
```

This is application-level code. gatevault provides `async_refresh` for the base rotation step, plus `create_access_token`, `create_refresh_token`, and `decode_token` if you want to build it yourself. The family tracking, persistence, and reuse detection above are yours to implement based on your requirements.

---

## Exception Hierarchy

All gatevault exceptions inherit from `GatevaultError`. You can catch it broadly or handle specific cases.

```
GatevaultError                          # Base exception for all gatevault errors
├── TokenError                          # Base for JWT-related errors
│   ├── TokenExpiredError               # Token has expired
│   ├── InvalidTokenError               # Token signature is invalid
│   └── TokenDecodeError                # Token is malformed or cannot be decoded
├── HashingError                        # Password hashing failed
└── GuardError                          # Base for auth guard errors
    ├── InvalidCredentialsError         # User not found during login, or during async_refresh if get_user_by_id is set
    └── UnauthorizedError               # Password mismatch or invalid token in guard
```

Internal PyJWT exceptions (`ExpiredSignatureError`, `InvalidSignatureError`, `DecodeError`, `InvalidTokenError`) are caught inside gatevault and re-raised as the appropriate gatevault exception. Callers never need to import PyJWT.

### Broad catch

```python
from gatevault import GatevaultError

try:
    payload = tm.decode_token(token)
except GatevaultError as e:
    return {"error": str(e)}
```

### Specific catches

```python
from gatevault import TokenExpiredError, InvalidTokenError, TokenDecodeError

try:
    payload = tm.decode_token(token)
except TokenExpiredError:
    return {"error": "Token expired"}
except TokenDecodeError:
    return {"error": "Malformed token"}
except InvalidTokenError:
    return {"error": "Invalid signature"}
```

### Login-specific catches

```python
from gatevault import InvalidCredentialsError, UnauthorizedError, GuardError

try:
    tokens = handler.login(email, password)
except InvalidCredentialsError:
    return {"error": "No account found"}
except UnauthorizedError:
    return {"error": "Wrong password"}
except GuardError as e:
    return {"error": f"Auth error: {e}"}
```

---

## ShortKeyWarning

A warning class emitted when the `TokenManager` secret key is shorter than 32 bytes.

```python
import warnings
from gatevault import TokenManager, ShortKeyWarning

with warnings.catch_warnings(record=True) as w:
    warnings.simplefilter("always")
    tm = TokenManager(secret_key="short", access_expiry_minutes=15, refresh_expiry_days=7)
    assert len(w) == 1
    assert issubclass(w[0].category, ShortKeyWarning)
```

This is a warning, not an error. During development a short key is convenient. In production, use at least 32 bytes.

---

## UserID Type

gatevault exports a `UserID` type alias for type hints:

```python
from typing import Union
from uuid import UUID

UserID = Union[int, str, UUID]
```

This is the type that `create_access_token` and `create_refresh_token` accept for the `user_id` parameter. You can use it in your own type hints:

```python
from gatevault import UserID, TokenManager

def create_user_token(tm: TokenManager, user_id: UserID) -> str:
    return tm.create_access_token(user_id=user_id)
```

---

## normalize_user_id(user_id: UserID) -> Union[int, str]

The default `user_id_encoder` used by `TokenManager`. Converts `int`, `str`, and `UUID` values into an encodable form. UUIDs are converted to strings. ints and strings pass through unchanged.

```python
from gatevault import normalize_user_id

normalize_user_id(42)                    # 42 (int)
normalize_user_id("user-abc")            # "user-abc" (str)
normalize_user_id(UUID("a1b2c3d4-...")) # "a1b2c3d4-..." (str)

normalize_user_id([1, 2, 3])  # raises TypeError
```

Raises `TypeError` if the value is not `int`, `str`, or `UUID`.
