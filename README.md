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
3. Create an instance and edit the configuration:
   - Set `invite_phrase` to your secret trigger phrase.
   - Add your private room/space IDs to `invite_rooms`.
   - Add admin Matrix IDs to `admins`.
4. Invite the bot to your public Welcome room.

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

## License

AGPL-3.0-or-later — see [LICENSE](LICENSE).
