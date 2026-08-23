# Render current state

The authenticated Render service `Mkmoon` is linked to `mkkh01/Mkmoon` on `main` and remains Paper-only with live trading disabled.

After replacing `DATABASE_URL` through the Render Environment editor with the latest password supplied by the user and triggering a rebuild, the service still exits during FastAPI lifespan startup with:

`asyncpg.exceptions.InvalidPasswordError: password authentication failed for user "postgres"`

Build succeeds. The current blocker is therefore the Supabase PostgreSQL credential/connection identity, not Python dependencies, TLS, Binance, or Redis.

The value supplied with the `sb_secret_` prefix is a Supabase API/secret key, not the PostgreSQL password, and is not used by the current direct-PostgreSQL implementation. It must not be placed in `DATABASE_URL`.

No secret values are recorded in this file.
