# SPDX-License-Identifier: AGPL-3.0-or-later
#
# InviteBot — A maubot plugin for welcoming guests into a private Matrix community.
# Listens for a trigger phrase in a public Welcome room and sends invites to
# one or more private rooms/spaces. Includes anti-spam, DM mode, persistent
# storage, and admin commands.
from __future__ import annotations

import time
from typing import Type

from mautrix.types import EventType, RoomID, UserID
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
        helper.copy("success_message")
        helper.copy("already_invited_message")
        helper.copy("error_message")
        helper.copy("admins")


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

        await self._send_response(evt, user_id, msg)

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
    ) -> None:
        if self.config["quiet_mode"]:
            # DM the user privately instead of replying in the public room.
            # Reuse an existing DM room if one already exists to avoid duplicates.
            try:
                dm_room = await self._get_or_create_dm_room(user_id)
                await self.client.send_text(dm_room, message)
            except Exception:
                self.log.exception(f"Failed to send DM to {user_id}")
        else:
            await evt.reply(message)

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
        await evt.reply(
            f"**InviteBot Status**\n"
            f"- Trigger phrase: `{phrase}`\n"
            f"- Rooms configured: {len(rooms)}\n"
            f"- Total invited: {count}\n"
            f"- Quiet mode: {'on' if quiet else 'off'}"
        )

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
            try:
                await self.client.invite_user(RoomID(room_id), user_id)
                success_count += 1
            except Exception:
                self.log.exception(f"Failed to invite {user_id} to {room_id}")

        if success_count > 0:
            async with self.database.acquire() as conn:
                await conn.execute(
                    "INSERT INTO invited_users (user_id, invited_at) VALUES ($1, $2)",
                    user_id, time.time(),
                )
            await evt.reply(f"✅ Re-invited {user_id} to {success_count} room(s).")
        else:
            await evt.reply(f"❌ Failed to re-invite {user_id}.")
