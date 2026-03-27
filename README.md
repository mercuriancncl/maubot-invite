# maubot-invite

A [maubot](https://github.com/maubot/maubot) plugin that invites guests to a private Matrix space (or room) when they type a configurable trigger phrase in a public Welcome room.

---

## Features

- **Trigger phrase** — configurable secret phrase guests type to request an invite
- **Multi-room support** — invite users to one or more private rooms/spaces at once
- **Anti-spam** — persistent database storage prevents duplicate invites
- **Quiet mode** — optionally DM the user instead of replying in the public room
- **Admin commands** — live phrase updates, status reporting, and forced re-invites

---

## Quick Start

1. Download the latest `.mbp` release (or build it yourself with `mbc build`).
2. Upload the plugin in the maubot web interface.
3. Create an instance and assign the bot's Matrix client.
4. Edit the instance configuration:
   - Set `invite_phrase` to your secret trigger phrase.
   - Add your private room/space IDs to `invite_rooms` (see **Room IDs** below).
   - Add admin Matrix IDs to `admins`.
5. Invite the bot to your public Welcome room.
6. Invite the bot to each private room/space in `invite_rooms` and ensure it has **power level 50** (Moderator) so it can send invites.

---

## Room IDs

Room IDs must be quoted in YAML because they start with `!`, which is a YAML tag indicator.

To find a room's ID: open the room in your Matrix client → **Room Settings → Advanced**.

> **Note:** On some Synapse deployments the room ID stored internally does **not** include the `:server` suffix even for local rooms. If the bot reports `Unknown room` when inviting, try using the bare ID (e.g. `"!abcXYZ123"`) instead of the full `"!abcXYZ123:your.server.com"` form.

```yaml
invite_rooms:
  - "!yourPrivateSpaceId:your.server.com"
```

---

## Configuration

| Key                       | Default                              | Description                                          |
|---------------------------|--------------------------------------|------------------------------------------------------|
| `invite_phrase`           | `"I'd like to join"`                 | The trigger phrase (case-insensitive)                |
| `invite_rooms`            | _(example IDs)_                      | List of room/space IDs to invite the user to         |
| `quiet_mode`              | `false`                              | DM the user instead of replying publicly             |
| `success_message`         | _(see base-config.yaml)_             | Message sent on successful invite (`{user}`, `{rooms}`) |
| `already_invited_message` | _(see base-config.yaml)_             | Message sent if user was already invited             |
| `error_message`           | _(see base-config.yaml)_             | Message sent if invite fails                         |
| `admins`                  | _(empty)_                            | Matrix IDs allowed to use admin commands             |

---

## Admin Commands

| Command                  | Description                              |
|--------------------------|------------------------------------------|
| `!setphrase <new phrase>`| Update the trigger phrase live           |
| `!status`                | Show current config and invite count     |
| `!reinvite <@user:host>` | Re-send invites to a previously invited user |

---

## Building

```bash
pip install maubot
mbc build
```

---

## Compatibility

Requires maubot with the **asyncpg** database interface (`database_type: asyncpg` in `maubot.yaml`). This is supported by maubot 0.4.0+ with either SQLite or PostgreSQL plugin databases.

---

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE).
