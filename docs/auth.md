# Accounts and sessions

People sign up with an email and a password, and the site remembers them with a signed token in a cookie. Every form is protected against cross-site request forgery. This page describes each piece and why it was chosen.

## Accounts

Accounts live in the `app_user` table. The name avoids `user`, which is a reserved word in Postgres: an unquoted `SELECT * FROM user` returns the current database role instead of the table.

- **Email** is trimmed and lower-cased before it is stored or looked up (`domain/accounts.py`). A `CHECK (email = lower(email))` keeps it that way, so the unique constraint is case-insensitive. No email is sent, so an address is checked only for a plausible shape, not for deliverability. Accounts are therefore not verified, which matters only once email notifications exist (post-MVP).
- **Passwords** must be 8 to 128 characters, with no rules on which characters they contain, as NIST SP 800-63B recommends. They are hashed with Argon2id through pwdlib's recommended settings and stored in PHC format, parameters included. When the defaults change, a successful sign-in replaces the old hash.
- **Registration** relies on the unique constraint rather than a lookup first, so two sign-ups with one email at the same time cannot both succeed.
- **Sign-in** gives the same answer, "Wrong email or password", for an unknown email and for a wrong password. It also takes about the same time for both, because an unknown email is checked against a dummy hash. Argon2 runs in a thread pool, so hashing does not block the event loop.

## Session cookie

A successful sign-up or sign-in sets `access_token`, a JWT signed with HS256 and `SECRET_KEY` (`auth/tokens.py`).

| Property | Value | Why |
|---|---|---|
| Claims | `sub` (user id), `iat`, `exp`, all required | Only HS256 is accepted, so `alg: none` and other algorithms are rejected |
| Lifetime | 7 days, no refresh | The cookie's `Max-Age` matches it |
| `HttpOnly` | yes | Scripts on the page cannot read the token |
| `SameSite` | `Lax` | Browsers leave the cookie off cross-site POSTs |
| `Secure` | in production (`APP_ENV=production`) | Local development runs on plain HTTP |

Each request decodes the token and loads the account. A missing, expired or forged token, or one for a deleted account, means the visitor is signed out. Signing out deletes the cookie.

The app does not start without a `SECRET_KEY` of at least 32 characters. The collector, the worker and Alembic do not need one.

**Trade-off.** Nothing is stored on the server, so a token cannot be revoked before it expires. A token stolen from a device stays valid for up to 7 days, and changing `SECRET_KEY` signs everyone out. That is acceptable while accounts hold only subscriptions. If revocation becomes necessary, a `token_version` column on `app_user` included in the token would do it without a session table.

## CSRF protection

The project uses double-submit cookies (`auth/csrf.py`):

1. A middleware gives each request a token from the `csrf_token` cookie. A visitor without the cookie gets a new random token, which the HTML response sets (`HttpOnly`, `SameSite=Lax`, `Secure` in production).
2. Templates put the token into every form as a hidden `csrf_token` field. `base.html` also puts it into the `X-CSRF-Token` header of every HTMX request (`hx-headers` on `<body>`).
3. Every state-changing route depends on `verify_csrf`. The token sent in the field or the header must equal the cookie, compared in constant time, or the request gets 403.
4. Signing in or up issues a new token, so a token that was in use before sign-in is not valid in the new session.

Another site can make a browser send the cookies, but it cannot read them, so it cannot send a matching token. `SameSite=Lax` already keeps the session cookie off cross-site POSTs. The token is a second, independent layer, and it also protects the sign-in form, where there is no session yet.

## Redirects

Pages that need an account depend on `require_user`. A signed-out visitor is redirected with 303 to `/login?next=<the page>`. An HTMX request gets 401 with `HX-Redirect` instead, so the whole page loads rather than the sign-in form being swapped into a fragment. After sign-in, `next` is followed only when it is a path on this site. It must start with one `/`, contain no backslash and no control characters, which rules out `//evil.example`, `https://…` and header injection.

## Out of scope for now

- Email verification and password reset need outgoing email, which is post-MVP.
- Rate limiting of sign-in and sign-up comes with the production deploy (stage 11), through the Redis token bucket the collectors already use.
- A Content Security Policy and HSTS are set in Caddy (stage 11).
