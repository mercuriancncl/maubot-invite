# SPDX-License-Identifier: AGPL-3.0-or-later
#
# InviteBot — A maubot plugin for welcoming guests into a private Matrix community.
# Listens for a trigger phrase in a public Welcome room and sends invites to
# one or more private rooms/spaces. Includes anti-spam, DM mode, persistent
# storage, and admin commands.
from __future__ import annotations

import asyncio
import html
import time
from typing import Type

from mautrix.errors import MForbidden, MLimitExceeded
from mautrix.types import EventID, EventType, Membership, RoomID, StateEvent, UserID
from mautrix.util.async_db import UpgradeTable, Connection
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

from maubot import Plugin, MessageEvent
from maubot.handlers import command, event


# ─────────────────────────────────────────────────────────────────────────────
# DATABASE SCHEMA
#
# maubot provides each plugin with its own isolated SQLite (or Postgres) database.
# We use an UpgradeTable to define and version our schema. Each registered function
# runs once in order, creating or altering tables as needed.
# If we add features later, we add a new @upgrade_table.register() function (v2, v3...)
# and maubot will run only the new migrations on existing installs.
# ─────────────────────────────────────────────────────────────────────────────

upgrade_table = UpgradeTable()


@upgrade_table.register(description="Initial revision — create invited_users table")
async def upgrade_v1(conn: Connection) -> None:
    # Create the table that stores which users have already been invited.
    # TEXT PRIMARY KEY means each Matrix user ID can only appear once.
    # invited_at stores when they were invited (as a Unix timestamp float).
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS invited_users (
            user_id    TEXT PRIMARY KEY,
            invited_at REAL NOT NULL
        )"""
    )


@upgrade_table.register(description="Add pending_cleanups table for post-join message deletion")
async def upgrade_v2(conn: Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS pending_cleanups (
            user_id           TEXT PRIMARY KEY,
            trigger_room_id   TEXT NOT NULL,
            trigger_event_id  TEXT NOT NULL,
            response_event_id TEXT
        )"""
    )

@upgrade_table.register(description="Add welcomed_users table for new-member announcements")
async def upgrade_v3(conn: Connection) -> None:
    await conn.execute(
        """CREATE TABLE IF NOT EXISTS welcomed_users (
            user_id      TEXT PRIMARY KEY,
            welcomed_at  REAL NOT NULL
        )"""
    )


# ─────────────────────────────────────────────────────────────────────────────
# CONFIG CLASS
#
# Tells maubot how to read and update our base-config.yaml settings.
# Every key in base-config.yaml needs a helper.copy() line here.
# ─────────────────────────────────────────────────────────────────────────────

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("invite_phrase")
        helper.copy("invite_rooms")
        helper.copy("quiet_mode")
        helper.copy("delete_after_join")
        helper.copy("success_message")
        helper.copy("already_invited_message")
        helper.copy("error_message")
        helper.copy("admins")
        helper.copy("welcome_rooms")
        helper.copy("welcome_message")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN PLUGIN CLASS
# ─────────────────────────────────────────────────────────────────────────────

class InviteBot(Plugin):
    config: Config

    @classmethod
    def get_config_class(cls) -> Type[Config]:
        return Config

    @classmethod
    def get_db_upgrade_table(cls) -> UpgradeTable:
        return upgrade_table

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()

    # ─────────────────────────────────────────────────────────────────────────
    # MESSAGE HANDLER
    #
    # Fires on every m.room.message event in any room the bot has joined.
    # We check the message body for the trigger phrase, then send invites.
    # ─────────────────────────────────────────────────────────────────────────

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent) -> None:
        # Ignore messages from the bot itself to prevent loops.
        if evt.sender == self.client.mxid:
            return

        # Check for the trigger phrase (case-insensitive substring match).
        body = evt.content.body.strip()
        phrase = self.config["invite_phrase"].strip()
        if phrase.lower() not in body.lower():
            return

        user_id = evt.sender

        # Check whether this user has already been invited (anti-spam).
        async with self.database.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT user_id FROM invited_users WHERE user_id = $1", user_id
            )
        if row:
            msg = self.config["already_invited_message"].format(user=user_id)
            await self._send_response(evt, user_id, msg)
            return

        # Send invites to all configured rooms/spaces.
        rooms = self.config["invite_rooms"]
        success_count = 0
        for room_id in rooms:
            try:
                await self.client.invite_user(RoomID(room_id), user_id)
                success_count += 1
            except Exception:
                self.log.exception(f"Failed to invite {user_id} to {room_id}")

        if success_count > 0:
            # Record the invite in the database to prevent repeat invites.
            async with self.database.acquire() as conn:
                await conn.execute(
                    "INSERT INTO invited_users (user_id, invited_at) VALUES ($1, $2)",
                    user_id, time.time(),
                )
            msg = self.config["success_message"].format(
                user=user_id, rooms=success_count
            )
        else:
            msg = self.config["error_message"].format(user=user_id)

        response_evt_id = await self._send_response(evt, user_id, msg)

        if self.config["delete_after_join"] and success_count > 0:
            async with self.database.acquire() as conn:
                await conn.execute(
                    """INSERT INTO pending_cleanups
                           (user_id, trigger_room_id, trigger_event_id, response_event_id)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (user_id) DO UPDATE SET
                           trigger_room_id   = excluded.trigger_room_id,
                           trigger_event_id  = excluded.trigger_event_id,
                           response_event_id = excluded.response_event_id""",
                    user_id, str(evt.room_id), str(evt.event_id),
                    str(response_evt_id) if response_evt_id else None,
                )

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER JOIN HANDLER
    #
    # When delete_after_join is enabled, watches for the invited user joining
    # any of the configured invite_rooms, then redacts the trigger message and
    # the bot's own response from the welcome room.
    # ─────────────────────────────────────────────────────────────────────────

    @event.on(EventType.ROOM_MEMBER)
    async def on_member(self, evt: StateEvent) -> None:
        if evt.content.membership != Membership.JOIN:
            return
        if str(evt.room_id) not in self.config["invite_rooms"]:
            return

        user_id = UserID(evt.state_key)

        # ── delete_after_join cleanup ────────────────────────────────────────
        if self.config["delete_after_join"]:
            async with self.database.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT trigger_room_id, trigger_event_id, response_event_id"
                    " FROM pending_cleanups WHERE user_id = $1",
                    user_id,
                )
                if row:
                    await conn.execute(
                        "DELETE FROM pending_cleanups WHERE user_id = $1", user_id
                    )

            if row:
                trigger_room = RoomID(row["trigger_room_id"])
                trigger_evt = EventID(row["trigger_event_id"])
                response_evt = EventID(row["response_event_id"]) if row["response_event_id"] else None

                try:
                    await self.client.redact(trigger_room, trigger_evt)
                except Exception:
                    self.log.warning(f"Could not redact trigger message {trigger_evt} in {trigger_room}")

                if response_evt:
                    try:
                        await self.client.redact(trigger_room, response_evt)
                    except Exception:
                        self.log.warning(f"Could not redact response message {response_evt} in {trigger_room}")

        # ── welcome announcement ─────────────────────────────────────────────
        welcome_rooms = self.config["welcome_rooms"]
        welcome_message = self.config["welcome_message"]
        if not welcome_rooms or not welcome_message:
            return

        async with self.database.acquire() as conn:
            already_welcomed = await conn.fetchrow(
                "SELECT user_id FROM welcomed_users WHERE user_id = $1", user_id
            )
            if already_welcomed:
                return
            await conn.execute(
                "INSERT INTO welcomed_users (user_id, welcomed_at) VALUES ($1, $2)",
                user_id, time.time(),
            )

        try:
            display_name = await self.client.get_displayname(user_id)
        except Exception:
            display_name = user_id

        mention_html = f'<a href="https://matrix.to/#/{user_id}">{html.escape(display_name)}</a>'
        plain_body = welcome_message.format(user=display_name)
        html_body = welcome_message.format(user=mention_html)

        for room_id in welcome_rooms:
            try:
                await self.client.send_message_event(
                    RoomID(room_id),
                    EventType.ROOM_MESSAGE,
                    {
                        "msgtype": "m.text",
                        "body": plain_body,
                        "format": "org.matrix.custom.html",
                        "formatted_body": html_body,
                    },
                )
            except Exception:
                self.log.warning(f"Could not send welcome message to {room_id} for {user_id}")

    # ─────────────────────────────────────────────────────────────────────────
    # HELPER: send a reply or a DM depending on quiet_mode
    # ─────────────────────────────────────────────────────────────────────────

    async def _get_or_create_dm_room(self, user_id: UserID) -> RoomID:
        # Check m.direct account data for an existing DM room with this user.
        try:
            direct_data = await self.client.get_account_data("m.direct")
            existing_rooms = direct_data.get(user_id, [])
            if existing_rooms:
                return RoomID(existing_rooms[0])
        except Exception:
            pass  # No account data yet or fetch failed; fall through to create.
        return await self.client.create_room(is_direct=True, invitees=[user_id])

    async def _send_response(
        self, evt: MessageEvent, user_id: UserID, message: str
    ) -> EventID | None:
        if self.config["quiet_mode"]:
            # DM the user privately instead of replying in the public room.
            # Reuse an existing DM room if one already exists to avoid duplicates.
            try:
                dm_room = await self._get_or_create_dm_room(user_id)
                await self.client.send_text(dm_room, message)
            except Exception:
                self.log.exception(f"Failed to send DM to {user_id}")
            return None
        else:
            return await evt.reply(message)

    # ─────────────────────────────────────────────────────────────────────────
    # ADMIN HELPERS
    # ─────────────────────────────────────────────────────────────────────────

    def _is_admin(self, user_id: UserID) -> bool:
        return user_id in self.config["admins"]

    # ─────────────────────────────────────────────────────────────────────────
    # ADMIN COMMANDS
    # ─────────────────────────────────────────────────────────────────────────

    @command.new("setphrase", help="Set the invite trigger phrase (admin only)")
    @command.argument("phrase", pass_raw=True, required=True)
    async def cmd_set_phrase(self, evt: MessageEvent, phrase: str) -> None:
        if not self._is_admin(evt.sender):
            await evt.reply("❌ You don't have permission to use this command.")
            return
        self.config["invite_phrase"] = phrase
        self.config.save()
        await evt.reply(f"✅ Invite phrase updated to: **{phrase}**")

    @command.new("status", help="Show bot status (admin only)")
    async def cmd_status(self, evt: MessageEvent) -> None:
        if not self._is_admin(evt.sender):
            await evt.reply("❌ You don't have permission to use this command.")
            return
        async with self.database.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM invited_users")
        rooms = self.config["invite_rooms"]
        phrase = self.config["invite_phrase"]
        quiet = self.config["quiet_mode"]
        welcome_rooms = self.config["welcome_rooms"]
        welcome_message = self.config["welcome_message"]
        await evt.reply(
            f"**InviteBot Status**\n"
            f"- Trigger phrase: `{phrase}`\n"
            f"- Rooms configured: {len(rooms)}\n"
            f"- Total invited: {count}\n"
            f"- Quiet mode: {'on' if quiet else 'off'}\n"
            f"- Welcome rooms: {len(welcome_rooms or [])}\n"
            f"- Welcome message: `{welcome_message}`"
        )

    @command.new("setwelcome", help="Set the welcome announcement message (admin only)")
    @command.argument("message", pass_raw=True, required=True)
    async def cmd_set_welcome(self, evt: MessageEvent, message: str) -> None:
        if not self._is_admin(evt.sender):
            await evt.reply("❌ You don't have permission to use this command.")
            return
        self.config["welcome_message"] = message
        self.config.save()
        await evt.reply(f"✅ Welcome message updated to: **{message}**")

    @command.new("reinvite", help="Re-invite a user who was already invited (admin only)")
    @command.argument("user", required=True)
    async def cmd_reinvite(self, evt: MessageEvent, user: str) -> None:
        if not self._is_admin(evt.sender):
            await evt.reply("❌ You don't have permission to use this command.")
            return
        if not user.startswith("@") or ":" not in user:
            await evt.reply("❌ Invalid Matrix user ID. Expected format: `@username:server.com`")
            return
        user_id = UserID(user)

        # Remove from database so the user can be invited again.
        async with self.database.acquire() as conn:
            await conn.execute(
                "DELETE FROM invited_users WHERE user_id = $1", user_id
            )

        rooms = self.config["invite_rooms"]
        success_count = 0
        for room_id in rooms:
            retries = 2
            while retries > 0:
                try:
                    await self.client.invite_user(RoomID(room_id), user_id)
                    success_count += 1
                    break
                except MForbidden as e:
                    if "already in the room" in str(e):
                        self.log.debug(f"{user_id} is already in {room_id}, skipping")
                        success_count += 1
                    else:
                        self.log.warning(f"No permission to invite {user_id} to {room_id}: {e}")
                    break
                except MLimitExceeded as e:
                    retries -= 1
                    retry_after = getattr(e, "retry_after_ms", None)
                    wait = (retry_after / 1000) if retry_after else 5
                    self.log.warning(f"Rate limited inviting {user_id} to {room_id}, waiting {wait}s")
                    await asyncio.sleep(wait)
                    if retries == 0:
                        self.log.error(f"Giving up on {room_id} after rate limit retries")
                except Exception:
                    self.log.exception(f"Failed to invite {user_id} to {room_id}")
                    break
            await asyncio.sleep(0.5)  # pace requests to avoid rate limiting

        if success_count > 0:
            async with self.database.acquire() as conn:
                await conn.execute(
                    "INSERT INTO invited_users (user_id, invited_at) VALUES ($1, $2)",
                    user_id, time.time(),
                )
            await evt.reply(f"✅ Re-invited {user_id} to {success_count} room(s).")
        else:
            await evt.reply(f"❌ Failed to re-invite {user_id}.")
