# maubot-invite — Development Notes

## Overview

**InviteBot** is a [maubot](https://github.com/maubot/maubot) plugin that listens in a public Matrix Welcome room for a configurable trigger phrase and automatically invites the user to one or more private rooms or spaces.

---

## Plugin Architecture

| File              | Purpose                                      |
|-------------------|----------------------------------------------|
| `maubot.yaml`     | Plugin metadata (ID, version, entry point)   |
| `base-config.yaml`| Default configuration values                 |
| `invitebot.py`    | Main plugin logic                            |

---

## Key Design Decisions

### Anti-Spam via Persistent Storage

The plugin records every invited user in a SQLite/PostgreSQL `invited_users` table. Before sending a new invite, it checks whether the sender is already in that table. This prevents a user from receiving duplicate invites by sending the trigger phrase multiple times.

### Case-Insensitive Matching

The trigger phrase comparison uses `.lower()` on both sides so guests don't need to type the phrase with exact capitalisation.

### Quiet Mode

When `quiet_mode: true` is set, the bot creates a direct message room with the user and sends responses there instead of replying publicly in the Welcome room. This avoids cluttering the public room with invite confirmations.

### Database Versioning

The `UpgradeTable` pattern from `mautrix.util.async_db` allows schema migrations to be added incrementally. Each registered function runs exactly once per install, in order. To add a new column in a future release, register a `v2` function without touching `v1`.

---

## Admin Commands

| Command              | Description                                           |
|----------------------|-------------------------------------------------------|
| `!setphrase <text>`  | Update the trigger phrase live (no restart needed)    |
| `!status`            | Display current phrase, room count, and invite totals |
| `!reinvite <@user>`  | Remove a user from the DB and re-send their invites   |

All commands are restricted to Matrix user IDs listed in the `admins` config key.

---

## Building the Plugin

Install the maubot CLI and run:

```bash
pip install maubot
mbc build
```

This produces a `.mbp` archive that can be uploaded via the maubot web interface.

---

## Configuration Reference

See `base-config.yaml` for all available options with inline documentation.

---

## Maubot / mautrix References

- [maubot plugin development guide](https://docs.mau.fi/maubot/dev/getting-started.html)
- [mautrix-python API](https://github.com/mautrix/python)
- [UpgradeTable docs](https://docs.mau.fi/python/latest/api/mautrix.util.async_db.html)
